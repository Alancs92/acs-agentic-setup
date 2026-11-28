//! `netmon sample`: one nettop snapshot -> deltas -> one SQLite transaction.

use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::delta::{iface_deltas, Engine};
use crate::errlog;
use crate::netinfo;
use crate::nettop::{nettop_command, nettop_timeout, run_nettop, Row, RunError};
use crate::paths;
use crate::rules::{Rules, DEFAULT_RULES};
use crate::store::{SampleRow, Store, WriteArgs};

pub const DB_FILE: &str = "netmon.db";
pub const RULES_FILE: &str = "rules.yaml";
pub const CONFIG_FILE: &str = "config";
const CONFIG_SEED: &str = "# netmon config: gateway MACs (one per line, aa:bb:cc:dd:ee:ff) that mean \"home\".\n\
# Seeded once from claude-ato's places.json `home_gateway_macs`; netmon has no runtime dependency on it.\n";

/// A boot-time change smaller than this is clock adjustment, not a reboot.
const BOOT_JITTER_S: i64 = 30;

#[derive(Debug, PartialEq, Eq)]
pub struct Outcome {
    pub processes: usize,
    pub app_deltas: usize,
    pub baseline: bool,
}

/// Create the data dir and seed `rules.yaml` / `config` if absent.
pub fn ensure_layout(dir: &Path) -> std::io::Result<()> {
    paths::ensure_dir(dir)?;
    for (name, body) in [(RULES_FILE, DEFAULT_RULES), (CONFIG_FILE, CONFIG_SEED)] {
        let p = dir.join(name);
        if !p.exists() {
            paths::write_private(&p, body.as_bytes())?;
        }
    }
    Ok(())
}

pub fn load_rules(dir: &Path) -> Result<Rules, String> {
    let p = dir.join(RULES_FILE);
    let src = fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
    Rules::parse(&src).map_err(|e| format!("{}: {e}", p.display()))
}

/// Normalised home gateway MACs; unparsable lines are skipped.
pub fn load_home_macs(dir: &Path) -> Vec<String> {
    fs::read_to_string(dir.join(CONFIG_FILE))
        .map(|s| {
            s.lines()
                .map(|l| l.split('#').next().unwrap_or("").trim())
                .filter_map(netinfo::normalize_mac)
                .collect()
        })
        .unwrap_or_default()
}

pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// `netmon sample --dry`: print parsed nettop rows, touch nothing.
pub fn dry(mut out: impl FnMut(&Row)) -> Result<usize, String> {
    let mut n = 0;
    run_nettop(&mut nettop_command(), nettop_timeout(), |r| {
        n += 1;
        out(&r);
    })
    .map_err(|e| format!("nettop: {e}"))?;
    Ok(n)
}

/// Why a sample was skipped. `kind` is one of `timeout|exit|spawn|io|netinfo|db`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    pub kind: &'static str,
    pub msg: String,
}

impl Failure {
    fn new(kind: &'static str, msg: impl Into<String>) -> Failure {
        Failure {
            kind,
            msg: msg.into(),
        }
    }
    fn db(what: &str, e: impl std::fmt::Display) -> Failure {
        Failure::new("db", format!("{what}: {e}"))
    }
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.msg)
    }
}

impl From<RunError> for Failure {
    fn from(e: RunError) -> Failure {
        let kind = match e {
            RunError::Spawn(_) => "spawn",
            RunError::Io(_) => "io",
            RunError::Timeout => "timeout",
            RunError::Exit(_) => "exit",
        };
        Failure::new(kind, format!("nettop: {e}"))
    }
}

/// Take one sample. On failure nothing partial is written; once the store is
/// open the skipped attempt is recorded (own tiny transaction) so reports can
/// show the gap, and every failure is appended to the capped error log.
pub fn run(dir: &Path, ts: i64) -> Result<Outcome, Failure> {
    let r = run_inner(dir, ts);
    if let Err(f) = &r {
        errlog::append_at(dir, ts, "sample", f.kind, &f.msg);
    }
    r
}

fn run_inner(dir: &Path, ts: i64) -> Result<Outcome, Failure> {
    ensure_layout(dir).map_err(|e| Failure::new("io", format!("data dir: {e}")))?;
    let mut store = Store::open(&dir.join(DB_FILE)).map_err(|e| Failure::db("db", e))?;
    match take(dir, ts, &mut store) {
        Ok(o) => Ok(o),
        Err(f) => {
            if let Err(e) = store.record_failure(ts, f.kind, &f.msg) {
                errlog::append_at(dir, ts, "sample", "db", &format!("record failure: {e}"));
            }
            Err(f)
        }
    }
}

fn take(dir: &Path, ts: i64, store: &mut Store) -> Result<Outcome, Failure> {
    let (prev, meta) = store.load_state().map_err(|e| Failure::db("db", e))?;

    let boot = netinfo::boot_time();
    let boot_changed =
        matches!((meta.boot_time, boot), (Some(a), Some(b)) if (a - b).abs() > BOOT_JITTER_S);

    // Stream nettop straight into the delta engine; nothing is buffered.
    let mut engine = Engine::new(&prev, boot_changed);
    let mut processes = 0usize;
    run_nettop(&mut nettop_command(), nettop_timeout(), |r| {
        processes += 1;
        engine.feed(r);
    })?; // no write on timeout/failure
    let (apps, procs) = engine.finish();

    let ifs =
        netinfo::read_interfaces().map_err(|e| Failure::new("netinfo", format!("netstat: {e}")))?;
    let (idelta, ifaces) = iface_deltas(&prev, &ifs, boot_changed);
    let gateway = netinfo::gateway_mac(&ifs);
    let home = gateway
        .as_ref()
        .is_some_and(|g| load_home_macs(dir).contains(g));

    let (sum_in, sum_out) = apps.iter().fold((0u64, 0u64), |a, d| {
        (
            a.0.saturating_add(d.bytes_in),
            a.1.saturating_add(d.bytes_out),
        )
    });
    let baseline = !prev.initialized;
    let interval_s = meta.last_ts.map(|l| (ts - l).max(1)).unwrap_or(0);

    let sample = (!baseline).then(|| SampleRow {
        ts,
        interval_s,
        iface: idelta,
        unattr_in: idelta.phys_in.saturating_sub(sum_in),
        unattr_out: idelta.phys_out.saturating_sub(sum_out),
        gateway_mac: gateway.as_deref(),
        home,
        apps: &apps,
    });
    store
        .write(&WriteArgs {
            sample,
            ts,
            boot_time: boot.or(meta.boot_time),
            procs: &procs,
            ifaces: &ifaces,
        })
        .map_err(|e| Failure::db("db write", e))?;

    // Daily housekeeping (rollup, archive). The sample is already committed,
    // so a failure here is reported but does not fail the run.
    if let Err(e) = store.maintain(&dir.join("archive")) {
        eprintln!("netmon: maintenance failed: {e}");
        errlog::append_at(dir, ts, "sample", "maint", &e.to_string());
    }
    Ok(Outcome {
        processes,
        app_deltas: apps.len(),
        baseline,
    })
}
