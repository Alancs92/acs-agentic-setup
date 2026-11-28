//! Pure delta engine: cumulative counters -> per-interval deltas.
//!
//! Rules (design.md "Delta rules"):
//! * first-ever run: baseline only, zero deltas;
//! * boot time changed: every counter restarted, so all are new (delta = current);
//! * new (pid,name) -> delta = current; pid reused by another name -> new;
//! * counter went backwards -> reset, delta = current;
//! * process vanished (nettop hides sockets-less processes, or a snapshot was
//!   partial) -> its state is KEPT, so a reappearing process is not booked
//!   twice; entries unseen for 24 h are dropped, at most 2,000 are kept, and a
//!   reboot clears them. Nothing negative is ever emitted.

use std::collections::{BTreeMap, HashMap};

use crate::nettop::Row;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcEntry {
    pub name: String,
    pub bytes_in: u64,
    pub bytes_out: u64,
    /// Unix time of the last run whose snapshot contained this process.
    pub last_seen: i64,
}

/// Vanished processes are forgotten after this long unseen.
pub const STATE_MAX_AGE_S: i64 = 24 * 3600;
/// Most vanished processes kept (the live snapshot is never truncated).
pub const STATE_MAX_ENTRIES: usize = 2000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IfKind {
    /// `en*` (Wi-Fi, Ethernet): counts toward the device total.
    Physical,
    /// `utun*`: VPN tunnel, recorded as a hint, never added to the total.
    Tunnel,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IfCounters {
    pub name: String,
    pub kind: IfKind,
    pub bytes_in: u64,
    pub bytes_out: u64,
}

/// What the previous run left behind.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct State {
    /// False until a first run has recorded a baseline.
    pub initialized: bool,
    /// Keyed by pid (unique among live processes); the name guards pid reuse.
    pub procs: HashMap<u32, ProcEntry>,
    /// Keyed by interface name.
    pub ifaces: HashMap<String, (u64, u64)>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppDelta {
    pub name: String,
    pub bytes_in: u64,
    pub bytes_out: u64,
}

/// One counter: `prev` is `None` for "never seen".
pub fn counter_delta(prev: Option<u64>, cur: u64, boot_changed: bool) -> u64 {
    match prev {
        Some(p) if !boot_changed && cur >= p => cur - p,
        // new, rebooted, or went backwards (reset): everything is new
        _ => cur,
    }
}

/// Streaming engine: feed rows as nettop prints them.
pub struct Engine<'a> {
    prev: &'a State,
    boot_changed: bool,
    now: i64,
    cur: HashMap<u32, ProcEntry>,
    deltas: BTreeMap<String, (u64, u64)>,
}

impl<'a> Engine<'a> {
    pub fn new(prev: &'a State, boot_changed: bool, now: i64) -> Self {
        Engine {
            prev,
            boot_changed,
            now,
            cur: HashMap::with_capacity(prev.procs.len().max(64)),
            deltas: BTreeMap::new(),
        }
    }

    pub fn feed(&mut self, row: Row) {
        if self.cur.contains_key(&row.pid) {
            return; // duplicate pid in one snapshot: keep the first
        }
        let (d_in, d_out) = if !self.prev.initialized {
            (0, 0) // baseline run
        } else {
            match self.prev.procs.get(&row.pid) {
                Some(p) if p.name == row.name => (
                    counter_delta(Some(p.bytes_in), row.bytes_in, self.boot_changed),
                    counter_delta(Some(p.bytes_out), row.bytes_out, self.boot_changed),
                ),
                _ => (row.bytes_in, row.bytes_out), // new process / pid reuse
            }
        };
        if d_in > 0 || d_out > 0 {
            let e = self.deltas.entry(row.name.clone()).or_insert((0, 0));
            e.0 = e.0.saturating_add(d_in);
            e.1 = e.1.saturating_add(d_out);
        }
        self.cur.insert(
            row.pid,
            ProcEntry {
                name: row.name,
                bytes_in: row.bytes_in,
                bytes_out: row.bytes_out,
                last_seen: self.now,
            },
        );
    }

    /// Deltas (one per app name, name-sorted, zero rows omitted) and the new
    /// state: this snapshot plus the bounded set of processes it did not show.
    pub fn finish(mut self) -> (Vec<AppDelta>, HashMap<u32, ProcEntry>) {
        // After a reboot every counter restarted: old entries are meaningless.
        if !self.boot_changed {
            let mut vanished: Vec<(&u32, &ProcEntry)> = self
                .prev
                .procs
                .iter()
                .filter(|(pid, e)| {
                    !self.cur.contains_key(pid)
                        && self.now.saturating_sub(e.last_seen) <= STATE_MAX_AGE_S
                })
                .collect();
            vanished.sort_by(|a, b| b.1.last_seen.cmp(&a.1.last_seen).then(a.0.cmp(b.0)));
            vanished.truncate(STATE_MAX_ENTRIES.saturating_sub(self.cur.len()));
            for (pid, e) in vanished {
                self.cur.insert(*pid, e.clone());
            }
        }
        let deltas = self
            .deltas
            .into_iter()
            .map(|(name, (i, o))| AppDelta {
                name,
                bytes_in: i,
                bytes_out: o,
            })
            .collect();
        (deltas, self.cur)
    }
}

/// Convenience wrapper over [`Engine`] for whole snapshots.
pub fn compute(prev: &State, cur: &[Row], boot_changed: bool) -> (Vec<AppDelta>, State) {
    compute_at(prev, cur, boot_changed, 0)
}

/// As [`compute`], with the current time (for the vanished-entry age limit).
pub fn compute_at(
    prev: &State,
    cur: &[Row],
    boot_changed: bool,
    now: i64,
) -> (Vec<AppDelta>, State) {
    let mut e = Engine::new(prev, boot_changed, now);
    for r in cur {
        e.feed(r.clone());
    }
    let (deltas, procs) = e.finish();
    (
        deltas,
        State {
            initialized: true,
            procs,
            ifaces: prev.ifaces.clone(),
        },
    )
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct IfaceDelta {
    pub phys_in: u64,
    pub phys_out: u64,
    pub vpn_in: u64,
    pub vpn_out: u64,
}

/// Per-interface deltas, summed by kind. Per-interface (not on the sum) so an
/// interface disappearing (Ethernet unplugged) cannot look like a counter reset.
pub fn iface_deltas(
    prev: &State,
    cur: &[IfCounters],
    boot_changed: bool,
) -> (IfaceDelta, HashMap<String, (u64, u64)>) {
    let mut d = IfaceDelta::default();
    let mut next = HashMap::with_capacity(cur.len());
    for c in cur {
        let (di, do_) = if !prev.initialized {
            (0, 0)
        } else {
            let p = prev.ifaces.get(&c.name);
            (
                counter_delta(p.map(|x| x.0), c.bytes_in, boot_changed),
                counter_delta(p.map(|x| x.1), c.bytes_out, boot_changed),
            )
        };
        match c.kind {
            IfKind::Physical => {
                d.phys_in = d.phys_in.saturating_add(di);
                d.phys_out = d.phys_out.saturating_add(do_);
            }
            IfKind::Tunnel => {
                d.vpn_in = d.vpn_in.saturating_add(di);
                d.vpn_out = d.vpn_out.saturating_add(do_);
            }
        }
        next.insert(c.name.clone(), (c.bytes_in, c.bytes_out));
    }
    (d, next)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(n: &str, pid: u32, i: u64, o: u64) -> Row {
        Row {
            name: n.into(),
            pid,
            bytes_in: i,
            bytes_out: o,
        }
    }
    fn ready(rows: &[Row]) -> State {
        let (_, s) = compute(
            &State {
                initialized: true,
                ..Default::default()
            },
            rows,
            false,
        );
        s
    }
    fn d(deltas: &[AppDelta], n: &str) -> (u64, u64) {
        deltas
            .iter()
            .find(|x| x.name == n)
            .map(|x| (x.bytes_in, x.bytes_out))
            .unwrap_or((0, 0))
    }

    #[test]
    fn first_ever_run_is_baseline_only() {
        let (deltas, st) = compute(&State::default(), &[row("a", 1, 1000, 2000)], false);
        assert!(deltas.is_empty());
        assert!(st.initialized);
        assert_eq!(st.procs[&1].bytes_in, 1000);
    }

    #[test]
    fn normal_increase() {
        let prev = ready(&[row("a", 1, 100, 200)]);
        let (deltas, _) = compute(&prev, &[row("a", 1, 150, 260)], false);
        assert_eq!(d(&deltas, "a"), (50, 60));
    }

    #[test]
    fn new_pid_delta_is_current() {
        let prev = ready(&[row("a", 1, 100, 200)]);
        let (deltas, st) = compute(&prev, &[row("a", 1, 100, 200), row("b", 2, 7, 9)], false);
        assert_eq!(d(&deltas, "b"), (7, 9));
        assert_eq!(d(&deltas, "a"), (0, 0));
        assert!(st.procs.contains_key(&2));
    }

    #[test]
    fn counter_decrease_is_reset_delta_is_current() {
        let prev = ready(&[row("a", 1, 1000, 1000)]);
        let (deltas, _) = compute(&prev, &[row("a", 1, 30, 1200)], false);
        assert_eq!(d(&deltas, "a"), (30, 200)); // in reset, out normal
    }

    #[test]
    fn pid_reuse_with_different_name_is_a_new_process() {
        let prev = ready(&[row("old", 5, 1000, 1000)]);
        let (deltas, st) = compute(&prev, &[row("new", 5, 40, 50)], false);
        assert_eq!(d(&deltas, "new"), (40, 50));
        assert_eq!(d(&deltas, "old"), (0, 0));
        assert_eq!(st.procs[&5].name, "new");
    }

    #[test]
    fn vanished_process_is_kept_without_negative_delta() {
        let prev = ready(&[row("a", 1, 100, 100), row("gone", 2, 500, 500)]);
        let (deltas, st) = compute(&prev, &[row("a", 1, 110, 100)], false);
        assert_eq!(d(&deltas, "gone"), (0, 0));
        assert!(st.procs.contains_key(&2));
        assert_eq!(d(&deltas, "a"), (10, 0));
    }

    #[test]
    fn empty_snapshot_then_return_does_not_rebook_lifetimes() {
        let prev = ready(&[row("big", 1, 40_000_000_000, 5)]);
        let (_, mid) = compute(&prev, &[], false);
        let (deltas, _) = compute(&mid, &[row("big", 1, 40_000_000_100, 5)], false);
        assert_eq!(d(&deltas, "big"), (100, 0));
    }

    #[test]
    fn vanished_entries_expire_after_24h() {
        let prev = ready(&[row("gone", 2, 500, 500)]); // last_seen 0
        let (_, st) = compute_at(&prev, &[], false, STATE_MAX_AGE_S);
        assert!(st.procs.contains_key(&2), "exactly 24h is still kept");
        let (_, st) = compute_at(&prev, &[], false, STATE_MAX_AGE_S + 1);
        assert!(!st.procs.contains_key(&2));
    }

    #[test]
    fn vanished_entries_are_capped_keeping_the_most_recent() {
        let mut prev = ready(&[]);
        for pid in 0..(STATE_MAX_ENTRIES as u32 + 50) {
            prev.procs.insert(
                pid,
                ProcEntry {
                    name: "p".into(),
                    bytes_in: 1,
                    bytes_out: 1,
                    last_seen: i64::from(pid),
                },
            );
        }
        let (_, st) = compute_at(&prev, &[row("live", 99_999, 1, 1)], false, 3000);
        assert_eq!(st.procs.len(), STATE_MAX_ENTRIES);
        assert!(st.procs.contains_key(&99_999), "live row always kept");
        assert!(!st.procs.contains_key(&0), "oldest dropped");
        assert!(st.procs.contains_key(&(STATE_MAX_ENTRIES as u32 + 49)));
    }

    #[test]
    fn reboot_clears_vanished_entries() {
        let prev = ready(&[row("gone", 2, 500, 500)]);
        let (_, st) = compute(&prev, &[row("a", 1, 1, 1)], true);
        assert!(!st.procs.contains_key(&2));
    }

    #[test]
    fn reboot_treats_everything_as_new() {
        let prev = ready(&[row("a", 1, 1_000_000, 1_000_000)]);
        // after reboot pid 1 is a different, young process with smaller counters
        let (deltas, _) = compute(&prev, &[row("a", 1, 500, 600)], true);
        assert_eq!(d(&deltas, "a"), (500, 600));
        // even if (by chance) its counter is larger than before
        let prev = ready(&[row("a", 1, 10, 10)]);
        let (deltas, _) = compute(&prev, &[row("a", 1, 500, 600)], true);
        assert_eq!(d(&deltas, "a"), (500, 600));
    }

    #[test]
    fn large_interval_is_still_a_plain_difference() {
        let prev = ready(&[row("a", 1, 100, 100)]);
        let (deltas, _) = compute(&prev, &[row("a", 1, 9_000_000_000_100, 100)], false);
        assert_eq!(d(&deltas, "a"), (9_000_000_000_000, 0));
    }

    #[test]
    fn same_app_name_many_pids_is_summed() {
        let prev = ready(&[row("h", 1, 0, 0), row("h", 2, 0, 0)]);
        let (deltas, _) = compute(&prev, &[row("h", 1, 10, 1), row("h", 2, 5, 2)], false);
        assert_eq!(deltas.len(), 1);
        assert_eq!(d(&deltas, "h"), (15, 3));
    }

    #[test]
    fn duplicate_pid_in_one_snapshot_is_counted_once() {
        let prev = ready(&[row("a", 1, 0, 0)]);
        let (deltas, _) = compute(&prev, &[row("a", 1, 10, 0), row("a", 1, 99, 0)], false);
        assert_eq!(d(&deltas, "a"), (10, 0));
    }

    fn ifc(n: &str, k: IfKind, i: u64, o: u64) -> IfCounters {
        IfCounters {
            name: n.into(),
            kind: k,
            bytes_in: i,
            bytes_out: o,
        }
    }
    fn ifstate(v: &[(&str, u64, u64)]) -> State {
        State {
            initialized: true,
            procs: HashMap::new(),
            ifaces: v
                .iter()
                .map(|(n, i, o)| (n.to_string(), (*i, *o)))
                .collect(),
        }
    }

    #[test]
    fn iface_baseline_is_zero() {
        let (d, next) = iface_deltas(
            &State::default(),
            &[ifc("en0", IfKind::Physical, 9, 9)],
            false,
        );
        assert_eq!(d, IfaceDelta::default());
        assert_eq!(next["en0"], (9, 9));
    }

    #[test]
    fn iface_physical_summed_tunnel_separate() {
        let prev = ifstate(&[("en0", 100, 100), ("en5", 0, 0), ("utun4", 50, 50)]);
        let cur = [
            ifc("en0", IfKind::Physical, 160, 130),
            ifc("en5", IfKind::Physical, 40, 10),
            ifc("utun4", IfKind::Tunnel, 80, 70),
        ];
        let (d, _) = iface_deltas(&prev, &cur, false);
        assert_eq!((d.phys_in, d.phys_out), (100, 40));
        assert_eq!((d.vpn_in, d.vpn_out), (30, 20));
    }

    #[test]
    fn interface_counter_drop_without_reboot_uses_current() {
        let prev = ifstate(&[("en0", 1_000_000, 1_000_000)]);
        let (d, _) = iface_deltas(&prev, &[ifc("en0", IfKind::Physical, 50, 2_000_000)], false);
        assert_eq!((d.phys_in, d.phys_out), (50, 1_000_000));
    }

    #[test]
    fn unplugged_interface_is_not_a_reset() {
        let prev = ifstate(&[("en0", 100, 100), ("en5", 5_000_000, 5_000_000)]);
        let (d, next) = iface_deltas(&prev, &[ifc("en0", IfKind::Physical, 110, 100)], false);
        assert_eq!((d.phys_in, d.phys_out), (10, 0));
        assert!(!next.contains_key("en5"));
    }

    #[test]
    fn new_interface_and_reboot_use_current() {
        let prev = ifstate(&[("en0", 100, 100)]);
        let (d, _) = iface_deltas(
            &prev,
            &[
                ifc("en0", IfKind::Physical, 110, 100),
                ifc("en5", IfKind::Physical, 7, 8),
            ],
            false,
        );
        assert_eq!((d.phys_in, d.phys_out), (17, 8));
        let (d, _) = iface_deltas(&prev, &[ifc("en0", IfKind::Physical, 50, 60)], true);
        assert_eq!((d.phys_in, d.phys_out), (50, 60));
    }
}
