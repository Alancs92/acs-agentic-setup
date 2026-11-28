//! `netmon sample` end to end against fake nettop/netstat/route/arp/sysctl.

mod common;

use common::{netmon, Fake};

fn run_ok(f: &Fake) -> String {
    let o = f.cmd(netmon()).arg("sample").output().unwrap();
    assert!(
        o.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&o.stderr)
    );
    String::from_utf8(o.stdout).unwrap()
}

fn count(f: &Fake, table: &str) -> i64 {
    f.db()
        .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
        .unwrap()
}

fn delta(f: &Fake, app: &str) -> (i64, i64) {
    f.db()
        .query_row(
            "SELECT SUM(bytes_in), SUM(bytes_out) FROM app_deltas WHERE proc=?1",
            [app],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap_or((0, 0))
}

#[test]
fn two_runs_produce_the_expected_deltas() {
    let f = Fake::new("two-runs");
    f.write_config("# home\na4:b:c:d:e:f # not it\n0a:0b:0c:0d:0e:0f\n");
    f.nettop(&[("a.10", 100, 200)]);
    f.netstat((1000, 2000), (10, 20));
    assert!(run_ok(&f).starts_with("baseline"));
    assert_eq!(count(&f, "samples"), 0, "baseline records no deltas");

    f.nettop(&[("a.10", 150, 260), ("b.11", 5, 6)]);
    f.netstat((1200, 2300), (40, 70));
    assert!(run_ok(&f).starts_with("sampled"));

    assert_eq!(count(&f, "samples"), 1);
    assert_eq!(delta(&f, "a"), (50, 60));
    assert_eq!(delta(&f, "b"), (5, 6));
    let (i, o, vi, vo, ui, uo, home, gw): (i64, i64, i64, i64, i64, i64, i64, Option<String>) = f
        .db()
        .query_row(
            "SELECT iface_in,iface_out,vpn_in,vpn_out,unattr_in,unattr_out,home,gateway_mac FROM samples",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?)),
        )
        .unwrap();
    assert_eq!(
        (i, o),
        (200, 300),
        "physical interface delta, tunnel excluded"
    );
    assert_eq!((vi, vo), (30, 50), "tunnel kept as a hint");
    assert_eq!(
        (ui, uo),
        (200 - 55, 300 - 66),
        "unattributed = iface - sum(apps)"
    );
    assert_eq!(home, 1);
    assert_eq!(gw.as_deref(), Some("0a:0b:0c:0d:0e:0f"));
}

#[test]
fn away_from_home_is_tagged_not_home() {
    let f = Fake::new("away");
    f.write_config("aa:aa:aa:aa:aa:aa\n");
    f.nettop(&[("a.1", 1, 1)]);
    f.netstat((10, 10), (0, 0));
    run_ok(&f);
    f.nettop(&[("a.1", 2, 2)]);
    f.netstat((20, 20), (0, 0));
    run_ok(&f);
    let home: i64 = f
        .db()
        .query_row("SELECT home FROM samples", [], |r| r.get(0))
        .unwrap();
    assert_eq!(home, 0);
}

#[test]
fn offline_has_no_gateway_and_is_not_home() {
    let f = Fake::new("offline");
    f.write_config("0a:0b:0c:0d:0e:0f\n");
    f.nettop(&[("a.1", 1, 1)]);
    f.netstat((10, 10), (0, 0));
    run_ok(&f);
    f.set(
        "route.txt",
        "route: writing to routing socket: not in table\n",
    );
    f.set("arp.txt", "");
    f.nettop(&[("a.1", 2, 2)]);
    f.netstat((20, 20), (0, 0));
    run_ok(&f);
    let (home, gw): (i64, Option<String>) = f
        .db()
        .query_row("SELECT home, gateway_mac FROM samples", [], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .unwrap();
    assert_eq!((home, gw), (0, None));
}

#[test]
fn timeout_run_leaves_the_db_unchanged() {
    let f = Fake::new("timeout");
    f.nettop(&[("a.10", 100, 200)]);
    f.netstat((1000, 2000), (0, 0));
    run_ok(&f);
    f.nettop(&[("a.10", 900, 900)]);
    f.netstat((5000, 5000), (0, 0));
    f.flag("hang", true);
    let t = std::time::Instant::now();
    let o = f
        .cmd(netmon())
        .arg("sample")
        .env("NETMON_NETTOP_TIMEOUT_MS", "400")
        .output()
        .unwrap();
    assert!(!o.status.success());
    assert!(t.elapsed() < std::time::Duration::from_secs(10));
    assert_eq!(count(&f, "samples"), 0);
    // state is still the baseline: the next good run diffs against it
    f.flag("hang", false);
    run_ok(&f);
    assert_eq!(delta(&f, "a"), (800, 700));
    assert!(
        f.data.join("last-error.txt").metadata().is_err(),
        "error file cleared on success"
    );
}

#[test]
fn helper_timeout_is_a_recorded_netinfo_failure() {
    let f = Fake::new("helper-timeout");
    f.nettop(&[("a.10", 100, 200)]);
    f.netstat((1000, 2000), (0, 0));
    run_ok(&f);
    f.flag("hang_netstat", true);
    let t = std::time::Instant::now();
    let o = f
        .cmd(netmon())
        .arg("sample")
        .env("NETMON_HELPER_TIMEOUT_MS", "300")
        .output()
        .unwrap();
    assert!(!o.status.success());
    assert!(t.elapsed() < std::time::Duration::from_secs(10));
    assert_eq!(count(&f, "samples"), 0);
    let kind: String = f
        .db()
        .query_row("SELECT kind FROM sample_failures", [], |r| r.get(0))
        .unwrap();
    assert_eq!(kind, "netinfo");
}

#[test]
fn failed_run_records_last_error() {
    let f = Fake::new("fail");
    f.nettop(&[("a.10", 1, 1)]);
    f.netstat((1, 1), (0, 0));
    run_ok(&f);
    f.flag("fail", true);
    assert!(!f
        .cmd(netmon())
        .arg("sample")
        .output()
        .unwrap()
        .status
        .success());
    assert!(f.data.join("last-error.txt").exists());
    assert_eq!(count(&f, "samples"), 0);
}

#[test]
fn failures_are_logged_recorded_and_surfaced_in_report_and_query() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fake::new("gaps");
    f.nettop(&[("a.10", 100, 200)]);
    f.netstat((1000, 2000), (0, 0));
    run_ok(&f);
    f.flag("hang", true);
    for _ in 0..2 {
        let o = f
            .cmd(netmon())
            .arg("sample")
            .env("NETMON_NETTOP_TIMEOUT_MS", "300")
            .output()
            .unwrap();
        assert!(!o.status.success());
    }
    f.flag("hang", false);
    f.flag("fail", true);
    assert!(!f
        .cmd(netmon())
        .arg("sample")
        .output()
        .unwrap()
        .status
        .success());
    f.flag("fail", false);

    // 1. the capped error log has one line per failure, 0600
    let log = f.data.join("netmon.log");
    let text = std::fs::read_to_string(&log).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(lines.len(), 3, "{text}");
    assert!(
        lines[0].ends_with("sample timeout nettop: timed out"),
        "{text}"
    );
    assert!(lines[2].contains(" sample exit "), "{text}");
    assert_eq!(
        std::fs::metadata(&log).unwrap().permissions().mode() & 0o777,
        0o600
    );

    // 2. failures are rows, and still no partial sample data
    assert_eq!(count(&f, "sample_failures"), 3);
    assert_eq!(count(&f, "samples"), 0);

    // 3. a good sample afterwards; report and query show the gap
    f.nettop(&[("a.10", 150, 260)]);
    f.netstat((1200, 2300), (0, 0));
    run_ok(&f);
    let o = f.cmd(netmon()).arg("report").output().unwrap();
    assert!(o.status.success());
    let out = String::from_utf8(o.stdout).unwrap();
    assert_eq!(out.lines().count(), 3, "{out}");
    assert!(
        out.lines()
            .next()
            .unwrap()
            .contains("3 FAILED (exit 1, timeout 2)"),
        "{out}"
    );
    let rep = std::fs::read_dir(f.data.join("reports"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    let rep = std::fs::read_to_string(rep).unwrap();
    assert!(rep.contains("\"gaps\":{\"failed_samples\":3,\"by_kind\":{\"exit\":1,\"timeout\":2},\"longest_interval_s\":"), "{rep}");
    let o = f
        .cmd(netmon())
        .args([
            "query",
            "--from",
            "2000-01-01",
            "--to",
            "2999-12-31",
            "--group-by",
            "day",
        ])
        .output()
        .unwrap();
    assert!(String::from_utf8(o.stdout)
        .unwrap()
        .contains("\"failed_samples\":3"));
}

#[test]
fn report_and_query_errors_are_logged() {
    let f = Fake::new("cmd-errors");
    f.nettop(&[("a.10", 1, 1)]);
    f.netstat((1, 1), (0, 0));
    run_ok(&f); // creates the data dir
    let o = f
        .cmd(netmon())
        .args(["report", "--date", "nope"])
        .output()
        .unwrap();
    assert!(!o.status.success());
    let o = f.cmd(netmon()).args(["query", "--bogus"]).output().unwrap();
    assert!(!o.status.success());
    let text = std::fs::read_to_string(f.data.join("netmon.log")).unwrap();
    assert!(
        text.contains(" report error `nope` is not a YYYY-MM-DD date"),
        "{text}"
    );
    assert!(
        text.contains(" query error unknown argument `--bogus`"),
        "{text}"
    );
}

#[test]
fn padded_process_names_are_stored_trimmed() {
    let f = Fake::new("trim");
    f.nettop(&[("Microsoft Edge .10", 1, 1)]);
    f.netstat((1, 1), (0, 0));
    run_ok(&f);
    f.nettop(&[("Microsoft Edge .10", 11, 21)]);
    f.netstat((50, 50), (0, 0));
    run_ok(&f);
    assert_eq!(delta(&f, "Microsoft Edge"), (10, 20));
}

#[test]
fn reboot_counts_everything_as_new() {
    let f = Fake::new("reboot");
    f.nettop(&[("a.10", 1_000_000, 1_000_000)]);
    f.netstat((9_000_000, 9_000_000), (0, 0));
    run_ok(&f);
    f.set(
        "boot.txt",
        "{ sec = 1795099999, usec = 1 } Thu Nov 19 21:33:19 2026\n",
    );
    f.nettop(&[("a.10", 500, 600)]);
    f.netstat((700, 800), (0, 0));
    run_ok(&f);
    assert_eq!(delta(&f, "a"), (500, 600));
    let (i, o): (i64, i64) = f
        .db()
        .query_row("SELECT iface_in, iface_out FROM samples", [], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .unwrap();
    assert_eq!((i, o), (700, 800));
}

#[test]
fn small_boottime_jitter_is_not_a_reboot() {
    let f = Fake::new("jitter");
    f.nettop(&[("a.10", 100, 100)]);
    f.netstat((1000, 1000), (0, 0));
    run_ok(&f);
    f.set("boot.txt", "{ sec = 1795002040, usec = 1 } x\n"); // +3 s
    f.nettop(&[("a.10", 130, 140)]);
    f.netstat((1100, 1100), (0, 0));
    run_ok(&f);
    assert_eq!(delta(&f, "a"), (30, 40));
}

#[test]
fn files_and_dir_are_private() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fake::new("perms");
    f.nettop(&[("a.10", 1, 1)]);
    f.netstat((1, 1), (0, 0));
    run_ok(&f);
    let mode = |p: &std::path::Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(&f.data), 0o700);
    for n in ["netmon.db", "rules.yaml", "config"] {
        assert_eq!(mode(&f.data.join(n)), 0o600, "{n}");
    }
}

#[test]
fn report_and_query_work_on_sampled_data() {
    let f = Fake::new("report");
    f.write_config("0a:0b:0c:0d:0e:0f\n");
    f.nettop(&[("Slack Helper.10", 100, 200)]);
    f.netstat((1000, 2000), (0, 0));
    run_ok(&f);
    f.nettop(&[("Slack Helper.10", 1_100, 1_200), ("mystery.5", 7, 8)]);
    f.netstat((3000, 4000), (0, 0));
    run_ok(&f);

    let o = f.cmd(netmon()).arg("report").output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let out = String::from_utf8(o.stdout).unwrap();
    assert_eq!(out.lines().count(), 3, "{out}");
    assert!(out.contains("work "), "{out}");
    let reports: Vec<_> = std::fs::read_dir(f.data.join("reports")).unwrap().collect();
    assert_eq!(reports.len(), 1);

    let o = f
        .cmd(netmon())
        .args([
            "query",
            "--from",
            "2000-01-01",
            "--to",
            "2999-12-31",
            "--group-by",
            "class,network",
            "--format",
            "csv",
        ])
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let out = String::from_utf8(o.stdout).unwrap();
    assert!(out.contains("ask,home,7,8,15"), "{out}");
    assert!(out.contains("work,home,1000,1000,2000"), "{out}");
}

#[test]
fn dry_run_prints_rows_and_does_not_touch_the_data_dir() {
    let f = Fake::new("dry");
    f.nettop(&[("a.10", 1, 2), ("b.c.11", 3, 4)]);
    let o = f.cmd(netmon()).args(["sample", "--dry"]).output().unwrap();
    assert!(o.status.success());
    assert_eq!(
        String::from_utf8(o.stdout).unwrap(),
        "a\t10\t1\t2\nb.c\t11\t3\t4\n"
    );
    assert!(!f.data.exists());
}
