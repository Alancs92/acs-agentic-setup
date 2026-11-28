//! Capped error log (`netmon.log` in the data dir).
//!
//! launchd sends stdout/stderr to /dev/null, so without this a failing sampler
//! is invisible. One line per error, appended and closed immediately (nothing
//! is held in memory). When the file exceeds [`MAX_BYTES`] it is renamed to
//! `netmon.log.1` (replacing any older one), so the total can never exceed
//! about twice the cap. Writing the log is best effort: every failure here is
//! swallowed, a sample must never fail because of its own log.

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use crate::paths;

pub const LOG_FILE: &str = "netmon.log";
pub const OLD_LOG_FILE: &str = "netmon.log.1";
/// Rotate once the live file is larger than this.
pub const MAX_BYTES: u64 = 256 * 1024;
/// Longest message kept per line.
const MAX_MSG: usize = 300;

/// Append one line (`<UTC time> <subcommand> <kind> <message>`) now.
pub fn append(dir: &Path, subcommand: &str, kind: &str, msg: &str) {
    append_at(dir, crate::sample::now(), subcommand, kind, msg);
}

/// As [`append`] with an explicit timestamp. Never fails, never creates `dir`.
pub fn append_at(dir: &Path, ts: i64, subcommand: &str, kind: &str, msg: &str) {
    let _ = try_append(dir, ts, subcommand, kind, msg);
}

fn try_append(dir: &Path, ts: i64, subcommand: &str, kind: &str, msg: &str) -> io::Result<()> {
    let path = dir.join(LOG_FILE);
    if fs::metadata(&path).is_ok_and(|m| m.len() > MAX_BYTES) {
        fs::rename(&path, dir.join(OLD_LOG_FILE))?;
    }
    let line = format_line(ts, subcommand, kind, msg);
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(&path)?;
    let _ = paths::tighten_file(&path);
    f.write_all(line.as_bytes())
}

/// One log line, newline-terminated. Control characters in the message become
/// spaces so one error is always exactly one line.
pub fn format_line(ts: i64, subcommand: &str, kind: &str, msg: &str) -> String {
    let msg: String = msg
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .take(MAX_MSG)
        .collect();
    format!("{} {subcommand} {kind} {msg}\n", utc(ts))
}

/// `YYYY-MM-DDTHH:MM:SSZ` for UTC epoch seconds (civil-from-days algorithm).
pub fn utc(ts: i64) -> String {
    let days = ts.div_euclid(86_400);
    let secs = ts.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        secs / 3600,
        secs % 3600 / 60,
        secs % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempDir;
    use std::os::unix::fs::PermissionsExt;

    fn read(p: &Path) -> String {
        fs::read_to_string(p).unwrap()
    }

    #[test]
    fn utc_formatting() {
        assert_eq!(utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(utc(1_700_000_000), "2023-11-14T22:13:20Z");
        assert_eq!(utc(1_709_164_800), "2024-02-29T00:00:00Z");
    }

    #[test]
    fn appends_one_line_per_error_with_0600() {
        let t = TempDir::new("errlog-basic");
        append_at(t.path(), 0, "sample", "timeout", "nettop: timed out");
        append_at(t.path(), 60, "report", "error", "bad\nthing\u{1}");
        let p = t.path().join(LOG_FILE);
        assert_eq!(
            read(&p),
            "1970-01-01T00:00:00Z sample timeout nettop: timed out\n\
             1970-01-01T00:01:00Z report error bad thing \n"
        );
        assert_eq!(
            fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[test]
    fn long_messages_are_truncated() {
        let l = format_line(0, "sample", "io", &"x".repeat(5_000));
        assert!(l.len() < 400 && l.ends_with("x\n"), "{}", l.len());
    }

    #[test]
    fn rotates_when_over_the_cap_and_keeps_one_old_file() {
        let t = TempDir::new("errlog-rotate");
        let p = t.path().join(LOG_FILE);
        let old = t.path().join(OLD_LOG_FILE);
        fs::write(&p, vec![b'a'; MAX_BYTES as usize + 1]).unwrap();
        fs::write(&old, b"ancient").unwrap();
        append_at(t.path(), 0, "sample", "db", "boom");
        assert_eq!(fs::metadata(&old).unwrap().len(), MAX_BYTES + 1);
        assert_eq!(read(&p), "1970-01-01T00:00:00Z sample db boom\n");
        assert_eq!(
            fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[test]
    fn total_size_stays_bounded_under_a_flood() {
        let t = TempDir::new("errlog-flood");
        let msg = "y".repeat(280);
        for n in 0..3_000 {
            append_at(t.path(), n, "sample", "timeout", &msg);
        }
        let size = |n: &str| fs::metadata(t.path().join(n)).map(|m| m.len()).unwrap_or(0);
        assert!(size(LOG_FILE) <= MAX_BYTES + 400);
        assert!(size(LOG_FILE) + size(OLD_LOG_FILE) <= 2 * MAX_BYTES + 800);
        let names: Vec<_> = fs::read_dir(t.path()).unwrap().collect();
        assert_eq!(names.len(), 2, "only netmon.log and netmon.log.1");
    }

    #[test]
    fn logging_failures_are_silent_and_never_create_the_dir() {
        let t = TempDir::new("errlog-missing");
        let gone = t.path().join("nope");
        append_at(&gone, 0, "sample", "db", "x"); // must not panic
        assert!(!gone.exists());
        // a directory squatting on the log path makes the open fail: still silent
        fs::create_dir(t.path().join(LOG_FILE)).unwrap();
        append_at(t.path(), 0, "sample", "db", "x");
    }
}
