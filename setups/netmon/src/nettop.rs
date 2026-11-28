//! nettop CSV parsing and a bounded, streaming subprocess runner.

use std::io::{self, BufRead, BufReader, Read};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

/// One per-process row from `nettop -P -L 1 -x -J bytes_in,bytes_out`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub name: String,
    pub pid: u32,
    pub bytes_in: u64,
    pub bytes_out: u64,
}

/// Parse one CSV line: `name.pid,<in>,<out>,`. The header (`,bytes_in,...`),
/// blank lines and anything not shaped like a process row return `None`.
/// Names may contain dots (and even commas): the pid is whatever follows the
/// last dot of the first field, the counters are the last two fields.
pub fn parse_line(line: &str) -> Option<Row> {
    let line = line.trim_end_matches(['\n', '\r']);
    let line = line.strip_suffix(',').unwrap_or(line);
    let mut it = line.rsplitn(3, ',');
    let out = it.next()?;
    let inn = it.next()?;
    let name_pid = it.next()?;
    let (name, pid) = name_pid.rsplit_once('.')?;
    // nettop pads some names ("Microsoft Edge "); trim here so storage, rules,
    // reports and the (name, pid) delta key all see the clean name.
    let name = name.trim();
    if name.is_empty() {
        return None;
    }
    Some(Row {
        name: name.to_string(),
        pid: pid.parse().ok()?,
        bytes_in: inn.parse().ok()?,
        bytes_out: out.parse().ok()?,
    })
}

#[derive(Debug)]
pub enum RunError {
    Spawn(io::Error),
    Io(io::Error),
    Timeout,
    /// Child exited non-zero.
    Exit(Option<i32>),
}

impl std::fmt::Display for RunError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RunError::Spawn(e) => write!(f, "spawn failed: {e}"),
            RunError::Io(e) => write!(f, "read failed: {e}"),
            RunError::Timeout => write!(f, "timed out"),
            RunError::Exit(c) => write!(f, "exited with status {c:?}"),
        }
    }
}

/// Longest line we will hold at once. Longer lines are delivered in chunks
/// (and will simply fail to parse), so memory stays bounded whatever the child
/// prints.
const MAX_LINE: u64 = 64 * 1024;

/// Run `cmd`, feeding each stdout line to `on_line` as it arrives (one reused
/// buffer, nothing accumulated). If the child has not closed stdout within
/// `timeout` it is killed. The child is always reaped. The single watchdog
/// thread is scoped: it is joined before this function returns.
///
/// The child must be a single process (use `exec` in wrapper scripts): killing
/// a shell would leave its children holding the pipe open.
pub fn stream_lines(
    cmd: &mut Command,
    timeout: Duration,
    mut on_line: impl FnMut(&str),
) -> Result<ExitStatus, RunError> {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child: Child = cmd.spawn().map_err(RunError::Spawn)?;
    let stdout = child.stdout.take().expect("stdout was piped");
    let child = Mutex::new(child);
    let (done_tx, done_rx) = mpsc::channel::<()>();
    let mut timed_out = false;
    let mut io_err: Option<io::Error> = None;
    let mut status: Option<ExitStatus> = None;

    thread::scope(|s| {
        let child_ref = &child;
        let watchdog = thread::Builder::new()
            .name("netmon-watchdog".into())
            .stack_size(64 * 1024)
            .spawn_scoped(s, move || {
                if let Err(RecvTimeoutError::Timeout) = done_rx.recv_timeout(timeout) {
                    let mut c = child_ref.lock().unwrap_or_else(|e| e.into_inner());
                    let _ = c.kill();
                    return true;
                }
                false
            })
            .expect("spawn watchdog");

        let mut reader = BufReader::with_capacity(8 * 1024, stdout);
        let mut buf: Vec<u8> = Vec::with_capacity(256);
        loop {
            buf.clear();
            match reader.by_ref().take(MAX_LINE).read_until(b'\n', &mut buf) {
                Ok(0) => break,
                Ok(_) => match std::str::from_utf8(&buf) {
                    Ok(s) => on_line(s),
                    Err(_) => on_line(&String::from_utf8_lossy(&buf)),
                },
                Err(e) => {
                    io_err = Some(e);
                    break;
                }
            }
        }
        if io_err.is_some() {
            let mut c = child.lock().unwrap_or_else(|e| e.into_inner());
            let _ = c.kill();
        }
        // Stdout is closed, but the child may not have exited. Keep the
        // watchdog armed (done_tx alive) until the child is reaped: if it
        // never exits, the watchdog kills it at the deadline and try_wait
        // then reports the status.
        loop {
            let polled = {
                let mut c = child.lock().unwrap_or_else(|e| e.into_inner());
                c.try_wait()
            };
            match polled {
                Ok(Some(st)) => {
                    status = Some(st);
                    break;
                }
                Ok(None) => thread::sleep(Duration::from_millis(2)),
                Err(e) => {
                    io_err.get_or_insert(e);
                    break;
                }
            }
        }
        drop(done_tx); // wakes the watchdog (Disconnected, not Timeout)
        timed_out = watchdog.join().unwrap_or(false);
    });

    let status = match status {
        Some(s) => s,
        None => {
            // try_wait failed: kill and reap so nothing is left behind.
            let mut child = child.into_inner().unwrap_or_else(|e| e.into_inner());
            let _ = child.kill();
            child.wait().map_err(RunError::Io)?
        }
    };
    if timed_out {
        return Err(RunError::Timeout);
    }
    if let Some(e) = io_err {
        return Err(RunError::Io(e));
    }
    Ok(status)
}

/// Stream nettop rows to `on_row`. Non-zero exit is an error (the caller then
/// writes nothing).
pub fn run_nettop(
    cmd: &mut Command,
    timeout: Duration,
    mut on_row: impl FnMut(Row),
) -> Result<(), RunError> {
    let status = stream_lines(cmd, timeout, |l| {
        if let Some(r) = parse_line(l) {
            on_row(r);
        }
    })?;
    if status.success() {
        Ok(())
    } else {
        Err(RunError::Exit(status.code()))
    }
}

/// Seconds nettop waits between its two internal snapshots (`-s`).
pub const NETTOP_DELAY_S: &str = "1";
/// Hard cap on a nettop run.
pub const NETTOP_TIMEOUT: Duration = Duration::from_secs(10);

/// `NETMON_NETTOP_TIMEOUT_MS` shortens the cap (integration tests only).
pub fn nettop_timeout() -> Duration {
    std::env::var("NETMON_NETTOP_TIMEOUT_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .map(Duration::from_millis)
        .unwrap_or(NETTOP_TIMEOUT)
}

/// The real command (`NETMON_NETTOP` overrides the program, for tests).
pub fn nettop_command() -> Command {
    let prog = std::env::var_os("NETMON_NETTOP").unwrap_or_else(|| "/usr/bin/nettop".into());
    let mut c = Command::new(prog);
    c.args([
        // No DNS resolution: we only read per-process byte counters, and
        // reverse lookups made nettop stall to the 10 s cap on a slow resolver.
        "-n",
        "-P",
        "-L",
        "1",
        "-x",
        "-J",
        "bytes_in,bytes_out",
        // Physical interfaces only: excludes loopback and traffic inside a VPN
        // tunnel (counted once, as the VPN client's encrypted traffic).
        "-t",
        "wifi",
        "-t",
        "wired",
        "-s",
        NETTOP_DELAY_S,
    ]);
    c
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    mod parse {
        use super::*;

        #[test]
        fn header_is_skipped() {
            assert_eq!(parse_line(",bytes_in,bytes_out,\n"), None);
        }

        #[test]
        fn simple_row() {
            assert_eq!(
                parse_line("nesessionmanage.386,100,200,\n"),
                Some(Row {
                    name: "nesessionmanage".into(),
                    pid: 386,
                    bytes_in: 100,
                    bytes_out: 200
                })
            );
        }

        #[test]
        fn dotted_names_split_on_last_dot() {
            let r = parse_line("com.apple.foo.123,1,2,").unwrap();
            assert_eq!((r.name.as_str(), r.pid), ("com.apple.foo", 123));
            let r = parse_line("2.1.327.19665,36670,72149,").unwrap();
            assert_eq!((r.name.as_str(), r.pid), ("2.1.327", 19665));
        }

        #[test]
        fn names_with_spaces_and_commas() {
            let r = parse_line("Microsoft Edge .1030,37884148,879704,").unwrap();
            assert_eq!(r.name, "Microsoft Edge");
            let r = parse_line("a,b.7,1,2,").unwrap();
            assert_eq!((r.name.as_str(), r.pid, r.bytes_in), ("a,b", 7, 1));
        }

        #[test]
        fn names_are_trimmed_but_internal_spaces_kept() {
            assert_eq!(parse_line("Edge .5,1,2,").unwrap().name, "Edge");
            assert_eq!(parse_line(" Edge.5,1,2,").unwrap().name, "Edge");
            assert_eq!(parse_line("  My  App \t.5,1,2,").unwrap().name, "My  App");
            assert_eq!(parse_line("   .5,1,2,"), None);
        }

        #[test]
        fn malformed_rows_are_none_not_panics() {
            for l in [
                "",
                "\n",
                ",",
                ",,,",
                "noPid,1,2,",
                "x.notapid,1,2,",
                "x.1,1,",
                "x.1,a,2,",
                "x.1,1,b,",
                ".5,1,2,",
                "x.1,-1,2,",
                "x.99999999999,1,2,",
                "x.1,99999999999999999999999,2,",
            ] {
                assert_eq!(parse_line(l), None, "line {l:?}");
            }
        }

        #[test]
        fn crlf_and_missing_trailing_comma_ok() {
            assert!(parse_line("a.1,1,2\r\n").is_some());
        }
    }

    #[test]
    fn nettop_is_limited_to_physical_interfaces() {
        let c = nettop_command();
        let args: Vec<String> = c
            .get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect();
        let joined = args.join(" ");
        assert!(joined.contains("-t wifi"), "{joined}");
        assert!(joined.contains("-t wired"), "{joined}");
    }

    #[test]
    fn nettop_skips_dns_resolution() {
        let c = nettop_command();
        assert!(c.get_args().any(|a| a == "-n"), "nettop must run with -n");
    }

    mod runner {
        use super::*;

        fn sh(script: &str) -> Command {
            let mut c = Command::new("sh");
            c.arg("-c").arg(script);
            c
        }

        #[test]
        fn hung_child_is_killed_within_timeout() {
            let t = Instant::now();
            let r = run_nettop(&mut sh("exec sleep 30"), Duration::from_millis(300), |_| {});
            assert!(matches!(r, Err(RunError::Timeout)), "{r:?}");
            assert!(
                t.elapsed() < Duration::from_secs(5),
                "took {:?}",
                t.elapsed()
            );
        }

        #[test]
        fn child_that_closes_stdout_then_hangs_is_killed_within_timeout() {
            let t = Instant::now();
            let r = run_nettop(
                &mut sh("exec 1>&-; exec sleep 30"),
                Duration::from_millis(300),
                |_| {},
            );
            assert!(matches!(r, Err(RunError::Timeout)), "{r:?}");
            assert!(
                t.elapsed() < Duration::from_secs(5),
                "took {:?}",
                t.elapsed()
            );
        }

        #[test]
        fn rows_are_streamed_through_the_callback() {
            let mut rows = Vec::new();
            run_nettop(
                &mut sh("printf ',bytes_in,bytes_out,\\na.1,1,2,\\nb.2,3,4,\\nc.3,5,6,\\n'"),
                Duration::from_secs(5),
                |r| rows.push(r),
            )
            .unwrap();
            assert_eq!(rows.len(), 3);
            assert_eq!(rows[2].name, "c");
        }

        #[test]
        fn nonzero_exit_is_an_error() {
            let r = run_nettop(
                &mut sh("echo a.1,1,2,; exit 3"),
                Duration::from_secs(5),
                |_| {},
            );
            assert!(matches!(r, Err(RunError::Exit(Some(3)))), "{r:?}");
        }

        #[test]
        fn missing_program_is_spawn_error() {
            let r = run_nettop(
                &mut Command::new("/nonexistent/nettop"),
                Duration::from_secs(1),
                |_| {},
            );
            assert!(matches!(r, Err(RunError::Spawn(_))));
        }

        #[test]
        fn overlong_line_is_bounded_and_does_not_hang() {
            let mut n = 0usize;
            let mut maxlen = 0usize;
            // ~300 KB with no newline, then a valid row.
            let r = stream_lines(
                &mut sh("head -c 300000 /dev/zero | tr '\\0' x; echo; echo a.1,1,2,"),
                Duration::from_secs(10),
                |l| {
                    n += 1;
                    maxlen = maxlen.max(l.len());
                },
            );
            assert!(r.is_ok());
            assert!(maxlen <= MAX_LINE as usize);
            assert!(n >= 5);
        }
    }
}
