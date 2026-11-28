//! `netmon sample`: one nettop snapshot -> deltas -> one SQLite transaction.

use std::fs;
use std::path::Path;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::delta::{iface_deltas, Engine, IfCounters, IfKind, State};
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
/// Whole-run backstop: a `sample` still going after this is logged and killed.
const RUN_DEADLINE: Duration = Duration::from_secs(60);

/// `NETMON_RUN_DEADLINE_MS` shortens the backstop (integration tests only).
fn run_deadline() -> Duration {
    std::env::var("NETMON_RUN_DEADLINE_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .map(Duration::from_millis)
        .unwrap_or(RUN_DEADLINE)
}

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
    // Backstop: whatever hangs (a helper, the DB, ...), launchd would never
    // start another run while this one lives. Log and exit non-zero instead.
    let (done_tx, done_rx) = mpsc::channel::<()>();
    let deadline = run_deadline();
    let log_dir = dir.to_path_buf();
    let watchdog = thread::Builder::new()
        .name("netmon-run-deadline".into())
        .stack_size(64 * 1024)
        .spawn(move || {
            if let Err(mpsc::RecvTimeoutError::Timeout) = done_rx.recv_timeout(deadline) {
                errlog::append_at(
                    &log_dir,
                    ts,
                    "sample",
                    "timeout",
                    &format!("run exceeded {}s deadline, exiting", deadline.as_secs_f32()),
                );
                eprintln!("netmon: sample run exceeded its deadline");
                std::process::exit(1);
            }
        })
        .ok();
    let r = run_inner(dir, ts);
    drop(done_tx);
    if let Some(w) = watchdog {
        let _ = w.join();
    }
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

/// What `kern.boottime` says happened since the last run.
#[derive(Debug, PartialEq, Eq)]
enum BootVerdict {
    Same,
    /// Boot time moved AND a physical interface counter went down.
    Reboot,
    /// Boot time moved but the counters kept growing: the wall clock stepped.
    ClockStep,
}

/// A real reboot always resets the `netstat` counters; a clock step (manual
/// change, big NTP correction) moves `kern.boottime` but not them.
fn boot_verdict(
    prev_boot: Option<i64>,
    boot: Option<i64>,
    prev: &State,
    ifs: &[IfCounters],
) -> BootVerdict {
    let moved = matches!((prev_boot, boot), (Some(a), Some(b)) if (a - b).abs() > BOOT_JITTER_S);
    if !moved {
        return BootVerdict::Same;
    }
    let dropped = ifs.iter().any(|c| {
        c.kind == IfKind::Physical
            && prev
                .ifaces
                .get(&c.name)
                .is_some_and(|p| c.bytes_in < p.0 || c.bytes_out < p.1)
    });
    if dropped {
        BootVerdict::Reboot
    } else {
        BootVerdict::ClockStep
    }
}

fn take(dir: &Path, ts: i64, store: &mut Store) -> Result<Outcome, Failure> {
    let (prev, meta) = store.load_state().map_err(|e| Failure::db("db", e))?;

    let boot = netinfo::boot_time();
    // Interface counters first: they are the cross-check that tells a real
    // reboot from a wall-clock step (both move `kern.boottime`).
    let ifs =
        netinfo::read_interfaces().map_err(|e| Failure::new("netinfo", format!("netstat: {e}")))?;
    let boot_changed = match boot_verdict(meta.boot_time, boot, &prev, &ifs) {
        BootVerdict::Reboot => true,
        BootVerdict::ClockStep => {
            errlog::append_at(
                dir,
                ts,
                "sample",
                "clock_step",
                &format!(
                    "kern.boottime moved {:?} -> {:?} but interface counters did not drop: \
                     treated as a clock change, not a reboot",
                    meta.boot_time, boot
                ),
            );
            false
        }
        BootVerdict::Same => false,
    };

    // Stream nettop straight into the delta engine; nothing is buffered.
    // A short or empty snapshot is not an error: the engine keeps state for
    // processes missing from it (bounded, see delta.rs), so they are not
    // re-booked when they reappear. Rejecting small snapshots instead would
    // wedge sampling for good after a legitimate drop (quitting a multi-
    // process app, Wi-Fi going away), since the baseline never updates.
    let mut engine = Engine::new(&prev, boot_changed, ts);
    let mut processes = 0usize;
    run_nettop(&mut nettop_command(), nettop_timeout(), |r| {
        processes += 1;
        engine.feed(r);
    })?; // no write on timeout/failure
    let (apps, procs) = engine.finish();

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
