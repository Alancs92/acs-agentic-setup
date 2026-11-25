//! Opt-in self-measurement for the budget test (`NETMON_SELF_STATS=1`).
//!
//! `/usr/bin/time -l` folds waited-for children (nettop, netstat, ...) into the
//! numbers it prints, which is not what the budget is about ("netmon itself,
//! excluding the nettop child"). `getrusage(RUSAGE_SELF)` is exactly the
//! process alone. This is the only `unsafe` in the crate: one libc call with a
//! `#[repr(C)]` struct that matches macOS's `struct rusage`.

#[repr(C)]
struct Timeval {
    sec: i64,
    usec: i32,
    _pad: i32,
}

#[repr(C)]
struct Rusage {
    utime: Timeval,
    stime: Timeval,
    // ru_maxrss (bytes on macOS) followed by 13 more `long` counters.
    maxrss: i64,
    rest: [i64; 13],
}

extern "C" {
    fn getrusage(who: i32, usage: *mut Rusage) -> i32;
}

const RUSAGE_SELF: i32 = 0;

/// `selfstats user_us=.. sys_us=.. maxrss_bytes=..`
pub fn line() -> Option<String> {
    // SAFETY: `Rusage` is plain old data matching the C layout; getrusage only
    // writes into it.
    let mut ru: Rusage = unsafe { std::mem::zeroed() };
    if unsafe { getrusage(RUSAGE_SELF, &mut ru) } != 0 {
        return None;
    }
    let us = |t: &Timeval| t.sec * 1_000_000 + i64::from(t.usec);
    let _ = ru.rest;
    Some(format!(
        "selfstats user_us={} sys_us={} maxrss_bytes={}",
        us(&ru.utime),
        us(&ru.stime),
        ru.maxrss
    ))
}

pub fn enabled() -> bool {
    std::env::var_os("NETMON_SELF_STATS").is_some()
}

#[cfg(test)]
mod tests {
    #[test]
    fn reports_plausible_numbers() {
        let l = super::line().unwrap();
        let rss: i64 = l.rsplit("maxrss_bytes=").next().unwrap().parse().unwrap();
        assert!(rss > 100_000, "{l}");
    }
}
