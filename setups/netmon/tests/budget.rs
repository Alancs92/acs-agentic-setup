//! Performance budgets (design.md "Budgets"), enforced on the RELEASE binary.
//!
//! Run explicitly:
//!   cargo build --release && cargo test --release --test budget -- --ignored --nocapture
//!
//! "netmon itself" is measured with `getrusage(RUSAGE_SELF)` via the opt-in
//! `NETMON_SELF_STATS` line (see src/selfstats.rs), because `/usr/bin/time -l`
//! folds waited-for children (nettop, netstat, ...) into its numbers.

mod common;

use common::{netmon, Fake};
use std::process::Command;
use std::time::Instant;

const MB: i64 = 1024 * 1024;
const RSS_BUDGET: i64 = 5 * MB;
const CPU_BUDGET_US: i64 = 50_000;
const DB_BUDGET: u64 = 50 * 1024 * 1024;
const YEAR_BUDGET: u64 = 100 * 1024 * 1024;

const DAYS_RAW: i64 = 35;
const SAMPLES_PER_DAY: i64 = 720; // every 2 minutes
const APPS_PER_SAMPLE: usize = 40; // apps with non-zero traffic in a 2-minute window
const APP_POOL: usize = 150; // distinct app names seen over time
const NETTOP_ROWS: u32 = 400;

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    /// Roughly log-uniform byte counts: a few hundred bytes to a few MB.
    fn bytes(&mut self) -> i64 {
        let e = self.next() % 5;
        (self.next() % 10u64.pow(e as u32 + 2)) as i64 + 40
    }
}

fn app_name(i: usize) -> String {
    format!("App Helper {:03}", i % APP_POOL)
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

/// Fill `days` days of 2-minute samples ending now, exactly the rows
/// `Store::write` would have produced, plus `proc_state` for the fake nettop.
fn fill_raw(f: &Fake, days: i64) {
    netmon::sample::ensure_layout(&f.data).unwrap();
    let s = netmon::store::Store::open(&f.data.join("netmon.db")).unwrap();
    let c = s.conn();
    let mut rng = Rng(0x9E3779B97F4A7C15);
    let end = now();
    let total = days * SAMPLES_PER_DAY;
    c.execute_batch("BEGIN").unwrap();
    {
        let mut ins_s = c
            .prepare(
                "INSERT INTO samples(ts,day,interval_s,iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,gateway_mac,home)
                 VALUES(?1,date(?1,'unixepoch','localtime'),120,?2,?3,0,0,?4,?5,'aa:bb:cc:dd:ee:ff',?6)",
            )
            .unwrap();
        let mut ins_a = c
            .prepare(
                "INSERT INTO app_deltas(sample_id,proc,bytes_in,bytes_out) VALUES(?1,?2,?3,?4)",
            )
            .unwrap();
        for n in 0..total {
            let ts = end - (total - n) * 120;
            let home = (n / 360) % 2; // flips twice a day
            ins_s
                .execute(rusqlite::params![
                    ts,
                    rng.bytes() * 40,
                    rng.bytes() * 5,
                    rng.bytes(),
                    rng.bytes(),
                    home
                ])
                .unwrap();
            let id = c.last_insert_rowid();
            // a different, overlapping window of the app pool every sample
            let start = (rng.next() as usize) % APP_POOL;
            for k in 0..APPS_PER_SAMPLE {
                ins_a
                    .execute(rusqlite::params![
                        id,
                        app_name(start + k),
                        rng.bytes(),
                        rng.bytes()
                    ])
                    .unwrap();
            }
        }
    }
    c.execute_batch("COMMIT").unwrap();
    // State left by the previous run: 400 live processes, one interface.
    c.execute_batch("BEGIN").unwrap();
    {
        let mut ins = c
            .prepare("INSERT INTO proc_state(pid,proc,bytes_in,bytes_out) VALUES(?1,?2,?3,?4)")
            .unwrap();
        for pid in 1000..1000 + NETTOP_ROWS {
            ins.execute(rusqlite::params![
                pid,
                app_name(pid as usize),
                1_000_000,
                1_000_000
            ])
            .unwrap();
        }
    }
    c.execute(
        "INSERT INTO iface_state VALUES('en0',1000000000,1000000000)",
        [],
    )
    .unwrap();
    c.execute("INSERT INTO meta VALUES('boot_time',1795002037)", [])
        .unwrap();
    c.execute("INSERT INTO meta VALUES('last_ts',?1)", [end - 120])
        .unwrap();
    c.execute_batch("COMMIT").unwrap();
}

/// The same 400 processes with counters advanced by `step`; 40 of them active.
fn set_nettop(f: &Fake, step: u64) {
    let rows: Vec<(String, u64, u64)> = (1000..1000 + NETTOP_ROWS)
        .map(|pid| {
            let grow = if (pid - 1000) % 10 == 0 {
                step * 1500
            } else {
                0
            };
            (
                format!("{}.{pid}", app_name(pid as usize)),
                1_000_000 + grow,
                1_000_000 + grow / 2,
            )
        })
        .collect();
    let refs: Vec<(&str, u64, u64)> = rows.iter().map(|(n, i, o)| (n.as_str(), *i, *o)).collect();
    f.nettop(&refs);
    f.netstat(
        (
            1_000_000_000 + step * 600_000,
            1_000_000_000 + step * 200_000,
        ),
        (0, 0),
    );
}

#[derive(Debug, Clone, Copy)]
struct Run {
    cpu_us: i64,
    rss: i64,
    wall_ms: u128,
}

fn run_sample(f: &Fake) -> Run {
    let t = Instant::now();
    let o = f
        .cmd(netmon())
        .arg("sample")
        .env("NETMON_SELF_STATS", "1")
        .output()
        .unwrap();
    let wall_ms = t.elapsed().as_millis();
    let err = String::from_utf8_lossy(&o.stderr).into_owned();
    assert!(o.status.success(), "{err}");
    let l = err
        .lines()
        .find(|l| l.starts_with("selfstats "))
        .expect("selfstats line");
    let get = |k: &str| -> i64 {
        l.split_whitespace()
            .find_map(|w| w.strip_prefix(k))
            .unwrap()
            .parse()
            .unwrap()
    };
    Run {
        cpu_us: get("user_us=") + get("sys_us="),
        rss: get("maxrss_bytes="),
        wall_ms,
    }
}

fn median(mut v: Vec<i64>) -> i64 {
    v.sort();
    v[v.len() / 2]
}

#[test]
#[ignore = "release budget run: cargo test --release --test budget -- --ignored --nocapture"]
fn sample_run_stays_within_budget() {
    if cfg!(debug_assertions) {
        panic!("budgets apply to the release build: run with `cargo test --release`");
    }
    let f = Fake::new("budget");
    fill_raw(&f, DAYS_RAW + 1); // 36 days: the first run's maintenance rolls one day up
    set_nettop(&f, 0);

    let maint = run_sample(&f); // includes the once-a-day rollup + class sync
    eprintln!("maintenance run: {maint:?}");

    let mut runs = Vec::new();
    for step in 1..=15 {
        set_nettop(&f, step);
        runs.push(run_sample(&f));
    }
    let cpu = median(runs.iter().map(|r| r.cpu_us).collect());
    let rss = runs.iter().map(|r| r.rss).max().unwrap();
    let wall = median(runs.iter().map(|r| r.wall_ms as i64).collect());
    let db = std::fs::metadata(f.data.join("netmon.db")).unwrap().len();
    eprintln!(
        "steady-state ({} runs): median cpu {} us, peak rss {} bytes ({:.2} MB), median wall {} ms (fake nettop), db {:.1} MB",
        runs.len(), cpu, rss, rss as f64 / MB as f64, wall, db as f64 / MB as f64
    );
    let apps: i64 = f
        .db()
        .query_row("SELECT COUNT(*) FROM app_deltas", [], |r| r.get(0))
        .unwrap();
    let nz: i64 = f
        .db()
        .query_row(
            "SELECT COUNT(*) FROM app_deltas WHERE sample_id=(SELECT MAX(id) FROM samples)",
            [],
            |r| r.get(0),
        )
        .unwrap();
    eprintln!("app_deltas rows: {apps}; rows in last sample: {nz}");

    assert!(rss < RSS_BUDGET, "peak RSS {rss} >= {RSS_BUDGET}");
    assert!(
        cpu < CPU_BUDGET_US,
        "median CPU {cpu} us >= {CPU_BUDGET_US}"
    );
    assert!(db < DB_BUDGET, "DB {db} >= {DB_BUDGET}");
    // The daily maintenance run is allowed to cost more, but must stay modest.
    assert!(maint.rss < RSS_BUDGET, "maintenance RSS {}", maint.rss);
    assert!(
        maint.cpu_us < 1_000_000,
        "maintenance CPU {} us",
        maint.cpu_us
    );
}

/// One year of storage: 35 days of raw 2-minute rows (steady state) plus 365
/// days of per-app-per-day rollups. Must fit in 100 MB.
#[test]
#[ignore = "storage projection: cargo test --release --test budget -- --ignored --nocapture"]
fn one_year_projection_fits_in_100mb() {
    let f = Fake::new("year");
    fill_raw(&f, DAYS_RAW);
    {
        let c = f.db();
        c.execute_batch("BEGIN").unwrap();
        let mut rng = Rng(42);
        {
            let mut r = c
                .prepare("INSERT INTO daily_rollup(day,proc,home,bytes_in,bytes_out) VALUES(?1,?2,?3,?4,?5)")
                .unwrap();
            let mut t = c
                .prepare("INSERT INTO daily_totals VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)")
                .unwrap();
            for d in 0..365 {
                // Hard upper bound: EVERY app in the pool, at both home and away.
                let day = format!("2025-{:02}-{:02}", d / 28 + 1, d % 28 + 1);
                for home in 0..2 {
                    for a in 0..APP_POOL {
                        r.execute(rusqlite::params![
                            day,
                            app_name(a),
                            home,
                            rng.bytes() * 500,
                            rng.bytes() * 100
                        ])
                        .unwrap();
                    }
                    t.execute(rusqlite::params![
                        day,
                        home,
                        1i64 << 31,
                        1i64 << 29,
                        1,
                        2,
                        1i64 << 20,
                        1i64 << 19,
                        360,
                        43200
                    ])
                    .unwrap();
                }
            }
        }
        c.execute_batch("COMMIT").unwrap();
    }
    let size = std::fs::metadata(f.data.join("netmon.db")).unwrap().len();
    let raw = {
        let c = f.db();
        let pages: i64 = c.query_row("PRAGMA page_count", [], |r| r.get(0)).unwrap();
        let ps: i64 = c.query_row("PRAGMA page_size", [], |r| r.get(0)).unwrap();
        pages * ps
    };
    eprintln!(
        "projected 1-year DB: {:.1} MB (file {:.1} MB; 35d raw + 365d x {} apps x 2 networks of rollups)",
        raw as f64 / MB as f64,
        size as f64 / MB as f64,
        APP_POOL
    );
    assert!(
        size < YEAR_BUDGET,
        "1-year DB {size} bytes >= {YEAR_BUDGET}"
    );
}

/// Reference numbers for the real nettop: wall, CPU, RSS per `-s` delay.
/// Not an assertion, a measurement (needs the real /usr/bin/nettop).
#[test]
#[ignore = "measures the real nettop"]
fn real_nettop_cost_by_delay() {
    // `NETMON_NETTOP_GAP_S`: pause between runs, to see whether nettop is slow
    // when it has not run recently (launchd runs it every 120 s).
    let gap: u64 = std::env::var("NETMON_NETTOP_GAP_S")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(40);
    for delay in ["1", "1", "2", "0"] {
        std::thread::sleep(std::time::Duration::from_secs(gap));
        let t = Instant::now();
        let o = Command::new("/usr/bin/time")
            .args([
                "-l",
                "/usr/bin/nettop",
                "-P",
                "-L",
                "1",
                "-x",
                "-J",
                "bytes_in,bytes_out",
                "-s",
                delay,
            ])
            .output()
            .unwrap();
        let wall = t.elapsed();
        let err = String::from_utf8_lossy(&o.stderr);
        let rows = String::from_utf8_lossy(&o.stdout).lines().count();
        let pick = |k: &str| {
            err.lines()
                .find(|l| l.contains(k))
                .unwrap_or("?")
                .trim()
                .to_string()
        };
        eprintln!(
            "nettop -s {delay}: wall {:?}, rows {rows}, status {:?} | {} | {}",
            wall,
            o.status.code(),
            pick("real"),
            pick("maximum resident")
        );
    }
}

/// One real `netmon sample` pair against the REAL nettop/netstat/route/arp,
/// in a temp data dir. Prints netmon's own CPU/RSS and the wall time.
#[test]
#[ignore = "runs the real nettop"]
fn real_sample_cost() {
    let dir = std::env::temp_dir().join(format!("netmon-real-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for i in 0..3 {
        let t = Instant::now();
        let o = Command::new(netmon())
            .arg("sample")
            .env("NETMON_DIR", &dir)
            .env("NETMON_SELF_STATS", "1")
            .output()
            .unwrap();
        eprintln!(
            "real sample #{i}: wall {:?}, ok={}, stdout={:?}, {}",
            t.elapsed(),
            o.status.success(),
            String::from_utf8_lossy(&o.stdout).trim(),
            String::from_utf8_lossy(&o.stderr)
                .lines()
                .last()
                .unwrap_or("")
        );
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
    let _ = std::fs::remove_dir_all(&dir);
}
