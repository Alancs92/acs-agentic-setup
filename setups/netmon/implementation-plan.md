# netmon Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship `netmon`: a run-and-exit Rust binary that samples `nettop` every 2 minutes, stores per-app deltas in SQLite, and reports daily GB by app, home vs elsewhere, work vs personal.

**Spec:** [`design.md`](design.md) — read first; every task argues from it.

**Tech stack:** Rust 1.93.1, `rusqlite` (`bundled`), one small rules parser (YAML or TOML — builder's call, justify), no async runtime.

## Global constraints

- Run-and-exit. No daemon, no threads that outlive `main`.
- Stream nettop stdout line-by-line; never buffer it whole. Hard 10 s timeout; kill the child.
- One SQLite transaction per `sample`.
- Budgets (tested, Task 11): netmon peak RSS < 5 MB, < 50 ms CPU per run, DB < ~50 MB.
- Data dir `0700`, files `0600`; nothing personal in the repo.
- Release profile: `opt-level="z"|"s"`, `lto`, `codegen-units=1`, `panic="abort"`, `strip`.
- **Docs-only step so far: no Cargo files exist yet.** Task 0 creates them.
- Tests: pure functions get unit tests with fixtures; no test touches the real `nettop` except the explicitly marked integration tests.
- Never run writing git commands from an agent; the orchestrator commits explicit paths.

## File structure (under `setups/netmon/`)

| File | Responsibility |
|---|---|
| `Cargo.toml` | Deps + size-tuned release profile |
| `src/main.rs` | CLI dispatch (`sample`, `report`) |
| `src/nettop.rs` | Spawn, stream-parse, timeout |
| `src/delta.rs` | Pure delta engine (pid reuse, reset, reboot) |
| `src/netinfo.rs` | Interface totals, gateway MAC, boot time |
| `src/store.rs` | SQLite schema, transaction, retention/rollup |
| `src/rules.rs` | Rules load + classification |
| `src/report.rs` | Daily JSON + 3-line summary |
| `src/paths.rs` | Data dir creation, perms |
| `tests/` | Integration + budget tests |
| `launchd/` | plist template (Task 12) |

---

### Task 0: Crate scaffold

**Files:** Create `Cargo.toml`, `src/main.rs`.
- [ ] Failing test: `cargo test` fails (no crate). Add a trivial `main` test asserting `netmon --help` text via a function `usage()`.
- [ ] Implement minimal `main` with `sample`/`report` stubs; release profile as in constraints.
- [ ] Verify: `cargo build --release && ls -l target/release/netmon` (record size in a checkpoint).

### Task 1: Data dir and permissions

**Files:** `src/paths.rs`.
- [ ] Failing test: `ensure_dir(tmp)` creates dir with mode `0700`; a pre-existing `0755` dir is tightened; created files are `0600`.
- [ ] Implement with `std::fs` + `PermissionsExt`. Default path `~/Library/Application Support/netmon/`, overridable by `NETMON_DIR` (tests use it).
- [ ] Verify: `cargo test paths`.

### Task 2: nettop CSV line parser (pure)

**Files:** `src/nettop.rs`.
- [ ] Failing tests with fixtures: header `,bytes_in,bytes_out,` skipped; `nesessionmanage.386,100,200,` → `("nesessionmanage",386,100,200)`; names containing dots (`com.apple.foo.123`) split on the **last** dot; malformed/short rows return `None`, never panic.
- [ ] Implement `parse_line(&str) -> Option<Row>` with no allocation beyond the name.
- [ ] Verify: `cargo test nettop::parse`.

### Task 3: nettop streaming runner with timeout

**Files:** `src/nettop.rs`.
- [ ] Failing test: runner takes a command (injectable) — a fake `sh -c 'sleep 30'` must be killed and return `Err(Timeout)` within ~timeout; a fake that prints 3 rows returns 3 rows streamed through a callback.
- [ ] Implement with `std::process::Command`, piped stdout, `BufRead::read_line` reusing one `String`, a timer thread or `wait_timeout`-style loop that kills the child; reap the child on every path (no zombies).
- [ ] Verify: `cargo test nettop::runner`; manual: `cargo run -- sample --dry` shows rows.
- [ ] Builder's call: test `-s` delay and flags to cut the 5 s wall time; record the choice in `design.md`.

### Task 4: Delta engine (pure) — the correctness core

**Files:** `src/delta.rs`.
- [ ] Failing tests, one per rule: normal increase; new pid → delta = current; counter decrease → reset (delta = current); pid reuse with a different name; process vanished (state dropped, no negative delta); reboot (boot time changed → drop all state, treat as new); first-ever run → baseline only, zero deltas; large interval (sleep gap) still correct.
- [ ] Implement `compute(prev: &State, cur: &[Row], boot_changed: bool) -> (Vec<Delta>, State)`.
- [ ] Verify: `cargo test delta`.

### Task 5: Network info

**Files:** `src/netinfo.rs`.
- [ ] Failing tests on pure parsers using fixtures from `route -n get -ifscope <en*> default` (the **physical** interface's gateway — never the plain default route, which is `utun*` under VPN), `arp -n <ip>` (zero-pad MAC octets like the claude-ato tracker), `sysctl kern.boottime`. Cases: offline → `None` gateway; VPN up (default route on `utun*`) → still resolves the `en*` gateway; Wi-Fi + Ethernet both up.
- [ ] Implement. Interface counters: builder's call (`getifaddrs` preferred; fall back to parsing `netstat -ibn` rows where field 2 starts `<Link`). **Total = sum of all `en*` interfaces; `utun*` summed separately as `vpn_bytes`, never added to the total.** Spawned helper commands must be bounded by timeout too.
- [ ] Verify: `cargo test netinfo`; manual print on this Mac.

### Task 6: SQLite store

**Files:** `src/store.rs`.
- [ ] Failing tests (temp DB): schema created idempotently; `write_sample` in one transaction is atomic (inject failure mid-write → nothing persisted); state round-trips; WAL/journal choice documented; file mode `0600`.
- [ ] Implement schema: `samples(ts, interval_s, iface_in, iface_out, vpn_in, vpn_out, gateway_mac, home)`, `app_deltas(sample_id, proc, bytes_in, bytes_out)`, `proc_state(proc, pid, bytes_in, bytes_out)`, `meta(boot_time, …)`, `daily_rollup(date, proc, home, bytes_in, bytes_out)`.
- [ ] Verify: `cargo test store`.

### Task 7: Retention and rollup

**Files:** `src/store.rs`.
- [ ] Failing tests: rows older than 35 days are summed into `daily_rollup` per (date, proc, home) then deleted; totals before == totals after; running twice is idempotent; recent rows untouched.
- [ ] Implement; run opportunistically from `sample` (at most once per day) so no second launchd job is needed.
- [ ] Verify: `cargo test retention`.

### Task 8: Rules and classification

**Files:** `src/rules.rs`.
- [ ] Failing tests: exact and prefix match on truncated names; first match wins; unmatched → `ask`; invalid file → clear error, not panic; default seed parses.
- [ ] Implement with the chosen format (justify YAML vs TOML by binary size/RSS measurement).
- [ ] Verify: `cargo test rules`.

### Task 9: `netmon sample` end-to-end

**Files:** `src/main.rs`.
- [ ] Failing integration test with a fake nettop script (injectable via `NETMON_NETTOP`): two consecutive runs produce expected deltas in the DB; a timeout run leaves the DB unchanged.
- [ ] Wire nettop + netinfo + delta + store; unattributed = interface delta − Σ app deltas, clamped ≥ 0 and stored.
- [ ] Verify: `cargo test --test sample`; manual: run twice on this Mac, inspect with `sqlite3`.

### Task 10: `netmon report`

**Files:** `src/report.rs`.
- [ ] Failing tests on a fixture DB: totals; home vs elsewhere split; per-class sums; unattributed line; reading a date that was rolled up gives the same totals as raw; empty day gives a valid zero report; JSON has `schema_version: 1`; 3-line summary format.
- [ ] Implement; write `reports/YYYY-MM-DD.json` (0600) and print the summary.
- [ ] Verify: `cargo test report`; manual on real data.

### Task 11: Perf-budget test

**Files:** `tests/budget.rs` (or a script `scripts/check-budget.sh` invoked by the test).
- [ ] Failing test first: it runs the **release** binary under `/usr/bin/time -l` against a fake nettop with ~400 rows and a DB pre-filled to the 35-day steady-state size, parses `maximum resident set size` (bytes on macOS) and CPU `user+sys`.
- [ ] Assert peak RSS **< 5 MB**, CPU **< 50 ms** (median of N runs to cut noise), DB file **< 50 MB** after simulated 35 days of 2-minute samples + rollup.
- [ ] If it fails, tune (crate features, `sqlite` pragmas such as small `cache_size`, avoid collecting rows into a Vec) — never loosen the budget without Alan.
- [ ] Verify: `cargo build --release && cargo test --release --test budget -- --ignored` (marked `#[ignore]`, run explicitly).

### Task 12: launchd plist and install script

**Files:** `launchd/dev.netmon.sample.plist.template`, `scripts/install.sh`.
- [ ] Failing test: a shell/Rust check that the rendered plist passes `plutil -lint` and contains `StartInterval` 120, `ProcessType` Background, `LowPriorityIO` true, positive `Nice`.
- [ ] Implement template + installer (copies binary, renders plist, `launchctl bootstrap`); install is a separate, explicitly approved step.
- [ ] Verify: `plutil -lint <rendered>`; after install, `launchctl print gui/$UID/dev.netmon.sample` and confirm rows appear after ~2 min.

### Task 13: Soak check and docs

- [ ] Run installed for 24 h; record RSS of the last sample run, DB growth, and any missed intervals. Update `README.md` status to `active`, add measured numbers to `design.md`, and confirm `setups/INDEX.md` entry.
