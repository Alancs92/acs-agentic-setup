# Design: netmon — per-device daily network usage by app

- **Date:** 2026-11-25
- **Status:** approved design (Alan, 2026-11-25) — built (Tasks 0–12 + scope additions), not yet installed. See "Build notes".
- **Ticket:** TIX-2611-037

## Intent / why

Alan wants, for his end-of-day debrief: how many GB this Mac used today, and
which apps/sites used it, mapped to work vs non-work. Other agents should be
able to reuse the data, hence it lives in `acs-agentic-setup`. He may switch
networks, so measurement is **on the device** and each sample is **tagged with
the network** (home or elsewhere), not read from a router.

He insisted on: **very memory efficient, no leaks, must not bog down memory or
kill other processes, runs every 2 minutes.**

## Goals

- Daily total GB in/out for this Mac, network-independent.
- Per-app GB, classified `work | personal | ask` via a rules file.
- GB split **home vs elsewhere** (home = configured gateway MAC list).
- A daily JSON report other agents can read, plus a 3-line text summary.
- Hard resource budgets, enforced by `scripts/check.sh` (below).

## Non-goals (v1)

- Per-domain/site attribution (later: browser history or DNS logs).
- Other devices (later: Android, NextDNS, router as additional sources).
- Wiring into the EOD debrief or claude-ato `household_gb` (later: they read
  netmon's daily JSON; netmon does not know about them).
- Packet capture, traffic shaping, blocking, any root/sudo operation.

## Anti-goals

- **Not a daemon.** No resident process, so leaks cannot accumulate.
- No async runtime, no network access of its own, no telemetry.
- Never touches or signals other processes beyond killing its own `nettop`
  child on timeout.
- No raw data leaves the machine.

## Constraints

- macOS (Darwin 25.x), no sudo. `nettop` is an Apple platform binary (closed
  source, signed, sealed read-only system volume) — acceptable to depend on.
- Rust 1.93.1 + cargo at `~/.cargo/bin`.
- Data local only, owner-only permissions.

## Measured facts (verified 2026-11-25, this Mac)

- `nettop -P -L 1 -x -J bytes_in,bytes_out` works **without sudo**. Output is
  CSV: header `,bytes_in,bytes_out,` then rows `procname.pid,<in>,<out>,`.
  Counters are **cumulative since process start**. Names are truncated to ~15
  chars (e.g. `nesessionmanage.386`).
- nettop cost: **5.07 s wall, 0.10 s CPU, 7.6 MB peak RSS**. Most wall time is
  waiting on its sample delay, not working.
- Prior art (read-only reference): `claude-ato/main/tools/tracker/main.swift`
  — hourly launchd tracker writing `net-samples.jsonl` with `netstat -ibn`
  interface totals, `gateway_mac` (`route -n get default` → `arp -n <gw>`),
  `kern.boottime` for counter resets, files under 0700 dirs.

## Approaches

| | Approach | Verdict |
|---|---|---|
| **A** | **Standalone Rust binary in `acs-agentic-setup`** | **PICKED** |
| B | Extend the claude-ato Swift tracker | Rejected: its hourly cadence is too coarse for per-process deltas (short-lived processes vanish between samples), and it would couple netmon to claude-ato. |
| C | Shell + JSONL | Rejected: delta/pid-reuse/rollup/report logic is ugly and fragile in shell. |
| — | Python variant of A | Rejected: the interpreter costs ~15–20 MB, more than nettop itself, blowing the memory story. |

Why A: a small static-ish native binary starts in milliseconds, has a flat
memory profile, and the logic (deltas, rollups, rules, report) is testable.

## Decided design

### Shape

Single Rust binary `netmon` in `setups/netmon/`. **Run-and-exit.** Two
subcommands:

- `netmon sample` — run by launchd.
- `netmon report --date YYYY-MM-DD` — daily JSON + 3-line text summary.

### `netmon sample` flow

1. Spawn `nettop -P -L 1 -x -J bytes_in,bytes_out`; **stream** stdout
   line-by-line (never buffer the whole output); hard **10 s timeout**, after
   which the child is killed and the run is aborted with no partial write.
2. Read interface totals (builder's call: `getifaddrs`/sysctl preferred over
   spawning `netstat -ibn` if cheaper), the default-route gateway MAC, and
   `kern.boottime`.
3. Load previous sample state from SQLite; compute **deltas per (proc, pid)**.
4. Handle edge cases (below).
5. Write everything in **one SQLite transaction**; exit.

### Delta rules (the correctness core)

Counters are per-process cumulative, so `delta = current − previous`:

- **New (proc,pid) not seen before:** delta = current (process started since
  the last sample, so all its bytes are new). Exception: first-ever run or
  first run after a reboot — see below.
- **Counter went backwards** (`current < previous`) → treat as reset, delta =
  current.
- **pid reuse:** same pid, different name → different key; the old key is
  treated as exited. Same name and pid with a lower counter → reset rule.
- **Process exited between samples:** its final bytes after the last sample
  are unobservable from nettop; they show up as **unattributed** (below).
- **Process missing from a snapshot** (nettop hides socket-less processes): its
  state is **kept**, so it is not re-booked when it reappears. Bounded: entries
  unseen for 24 h are dropped, at most 2,000 are kept, a reboot clears them.
- **Empty or partial snapshot:** a valid sample. Retained state (above) keeps
  the next delta honest, and the interface total is still recorded. It is *not*
  rejected: the baseline only updates on success, so rejecting small snapshots
  would wedge sampling for good after a legitimate drop (quitting a
  multi-process app, Wi-Fi going away).
- **Reboot** (`kern.boottime` changed *and* interface counters went down): all
  process counters restarted; drop prior per-process state and treat all as new.
- **First-ever run:** record baseline, emit no deltas (avoids attributing
  lifetime totals to one 2-minute window).
- **Sleep/wake:** launchd does not fire while asleep, so there is no catch-up
  storm. The next sample after wake covers the whole sleep gap; that is a
  correct delta, just over a longer window (store `interval_s`).

### Interface totals and unattributed

**Count physical interfaces only** (`en*`: Wi-Fi, Ethernet), summed — never the
default-route interface. With the Harrison VPN on, the default route is a
`utun*` tunnel: its bytes exclude encryption overhead and are already inside the
physical counters, so counting it would undercount (or double-count if added).
Summing all `en*` also survives a Wi-Fi ↔ Ethernet switch mid-interval.
Tunnel (`utun*`) deltas are recorded separately as a `vpn_bytes` hint (likely
work), not added to the total.

Interface delta (bytes in/out on the physical interfaces) is the source of truth
for the **total**. `unattributed = interface delta − Σ per-app deltas`, clamped
at 0 and recorded, so the daily total is right even when processes exit
unobserved or nettop misses traffic.

**Per-app numbers cover the same interfaces.** nettop runs with
`-t wifi -t wired` (verified on macOS: the filter works; 64 rows vs 101
unfiltered), so per-app counters include only sockets on physical interfaces.
That excludes loopback (dev servers, Docker, local proxies), and traffic inside
a VPN tunnel is *not* counted for the app that sent it: it appears once, as the
encrypted traffic of the VPN client process, which `rules.yaml` classes as
`work` (consistent with "VPN is work"). Hence Σ per-app (and every per-class
sum) is **bounded by the interface total**; the remainder is `unattributed`.
Because the clamp would otherwise hide a violation, `netmon report` adds a
`warnings` entry (and a `WARNING` on summary line 1) when Σ apps exceeds the
total by more than 5 % for the day.

**Counter checks.** A `kern.boottime` change only counts as a reboot if a
physical interface's counter also went *down* (a real reboot resets `netstat`
counters; a clock step does not). Otherwise it is logged as `clock_step`,
the stored boot time is updated, and nothing is re-booked. Interface counters
that drop with an unchanged boot time are a reset: delta = current, per
interface.

### Network tag

Each sample row stores the gateway MAC (nullable when offline) and a derived
`home` boolean (MAC ∈ configured home list). Resolve the gateway from the
**physical interface's** route (e.g. `route -n get -ifscope en0 default`, or the
DHCP router for the primary `en*`), not the default route — under VPN the
default route is `utun*` and has no gateway MAC. The home list lives in netmon's
own `config`, seeded once from claude-ato's `places.json` `home_gateway_macs`
(no runtime dependency on claude-ato). Reports split GB home vs elsewhere
by summing deltas by that tag.

### launchd

`StartInterval=120`, `ProcessType=Background`, `LowPriorityIO=true`, positive
`Nice`. launchd never overlaps instances of one job. Plist is written by the
builder later (not part of this docs-only step).

### Budgets — enforced by `scripts/check.sh`

Measured with `/usr/bin/time -l` on the **release** binary, netmon itself
(excluding the nettop child):

- peak RSS **< 5 MB**
- CPU **< 50 ms** per run
- DB size **< ~50 MB** via retention: raw 2-minute deltas kept **35 days**, then
  rolled up to one row per app per day.

### Report

`netmon report --date YYYY-MM-DD` emits daily JSON and a 3-line text summary
(total GB; home vs elsewhere; top apps by class). Per-app classification comes
from `rules.yaml` (`work | personal | ask`) matching **truncated proc names or
prefixes**; unmatched apps are `ask`. Unattributed is reported as its own line.

### Storage and permissions

Under `~/Library/Application Support/netmon/` (decided here; builder may move
it but must state it): dir `0700`, files `0600`. Contents: `netmon.db`,
`rules.yaml`, `config` (home MAC list), and `reports/YYYY-MM-DD.json`. Nothing
under the repo.

### Dependency hygiene

Minimal crates: `rusqlite` with `bundled` (and justify the size hit against the
5 MB RSS budget — measure, don't assume), one tiny parser for rules (YAML or
TOML — builder's call, justify), `serde`/`serde_json` only if the report needs
it. No async runtime. Release profile tuned for size: `opt-level="z"` (or
`"s"`), `lto=true`, `codegen-units=1`, `panic="abort"`, `strip=true`.

## What's left to the builder

- nettop flags: test a shorter sample delay (`-s`) and other flags to cut the
  5 s wall time; verify `-P -L 1` semantics (one sample, per-process).
- Interface counters source (getifaddrs vs sysctl vs `netstat -ibn`).
- Rules format (YAML vs TOML) and the exact schema.
- SQLite schema, indices, and the rollup/retention job (run inside `sample`
  occasionally, or a separate `netmon maintain`).
- launchd plist and install script.
- Exact JSON schema of the daily report (version it: `schema_version: 1`).

## Builder check

### Answered (answers belong in this doc)

- *Daemon or run-and-exit?* Run-and-exit.
- *Language?* Rust, no async runtime.
- *Needs sudo?* No — `nettop -P -L 1 -x` works unprivileged.
- *How are deltas computed?* Per (proc,pid), see "Delta rules".
- *How is the total kept right?* Interface delta is authoritative; unattributed
  = interface delta − Σ app deltas.
- *What is "home"?* Gateway MAC ∈ configured list.
- *Where does data live / perms?* `~/Library/Application Support/netmon/`,
  0700/0600.
- *Retention?* Raw 35 days, then per-app-per-day rollups.
- *Budgets?* RSS < 5 MB, CPU < 50 ms, DB < ~50 MB, tested.
- *Sleep?* No sampling while asleep; the post-wake delta spans the gap.

### Minor (decide; mark as builder's call)

- Interface counter source (getifaddrs/sysctl vs `netstat -ibn`).
- YAML vs TOML for rules, and the matching syntax (prefix vs glob).
- Exact `nettop -s` delay and extra flags.
- SQLite schema/indices; where the rollup job runs.
- Report JSON field names (keep `schema_version`).
- Which interface(s) count as "active" when several are up (e.g. Wi-Fi + VPN
  tunnel double-counting).
- Handling nettop output rows that are not `name.pid` shaped.

### Alan's call — answered 2026-11-25

1. **Rules seed (`rules.yaml`, app level for v1):**
   - **work:** browsers (Chrome, Safari, Arc, Firefox, Edge — Alan: "mostly
     work"), Slack, Zoom, Teams, VS Code, Cursor, Docker, `git`, `ssh`, Claude
     (app + `claude` CLI / `node` running it), the Harrison VPN client.
   - **personal:** Spotify, Music, Steam.
   - **ask:** everything unmatched (shown in the debrief to label).
   - **Domain seed for the later per-domain phase (not v1):** work =
     `github.com`/`githubusercontent.com`, `atlassian.net`/`atlassian.com`,
     `zoom.us`, `harrison.ai`, Grafana (`grafana.net`/`grafana.com`),
     `claude.ai`/`anthropic.com`. Record it in `rules.yaml` now under a
     `domains:` key that v1 ignores, so it is ready when domains land.
2. **Home gateway MACs:** netmon keeps its own list, seeded once from
   claude-ato's `home_gateway_macs`. No runtime dependency.
3. **VPN / tunnel:** total = physical `en*` interfaces; `utun*` recorded as a
   `vpn_bytes` work hint, never added (see "Interface totals").
4. **Retention:** raw 35 days; daily per-app rollups kept indefinitely (a few
   KB/day).
5. **Report location:** `~/Library/Application Support/netmon/reports/`; no
   vault mirror for now.

## Build notes (2026-11-25) — decisions and measurements

**Scope additions approved by Alan during the build:** keep SQLite; < 100 MB per
year total; daily rollups older than 5 years archived to per-year CSV; analysis
friendly schema (UTC `ts` + local `day`, `schema_version`, SQL views,
[`SCHEMA.md`](SCHEMA.md)); `netmon query` for streamed retrieval.

**Builder's calls**

- **Crates:** only `rusqlite` (`bundled`, default features off). No serde, no
  YAML/TOML crate, no `libc` crate, no async runtime. JSON is hand-written
  (string escaping unit-tested); `rules.yaml` is a deliberately tiny *strict*
  YAML subset (three keys, `- pattern` lists, `domains:` block skipped) that
  rejects anything else with a line number.
- **Interface counters:** `netstat -ibn` parsed streaming (indexed from the end
  of the line, since tunnels have no Address column), not `getifaddrs` FFI:
  keeps the crate safe Rust. Counters are tracked **per interface** so an
  unplugged adapter never looks like a counter reset. Gateway MAC: first
  physical interface that carried traffic (`en0` first) via
  `route -n get -ifscope enX default` then `arp -n`.
- **Journal:** WAL, `synchronous=NORMAL`, `cache_size` 16 KiB. WAL so a
  paused reader (`netmon query | less`) can never block the sampler. Atomic per
  sample; a power loss can lose only the last commit.
- **Whole-run deadline:** `sample` exits non-zero with a `timeout` log line
  after 60 s, whatever it is stuck on (launchd would never start a second
  run). The child runner keeps its deadline until the child is reaped, not just
  until stdout closes.
- **Maintenance** (rollup, archive) records its attempt day whatever the
  outcome (once a day, logged once); the archive merges into an existing
  `<year>-*.csv` (existing rows win) and never overwrites it.
- **Boot detection:** `kern.boottime` changes of <= 30 s are treated as clock
  adjustment, not a reboot (a false reboot would book lifetime counters as one
  window's traffic).
- **Unattributed** is stored in `samples` (not as a fake app) and summed in
  `daily_totals`; per-app queries exclude it, interface totals include it.
- **Summary line count:** `netmon report` prints exactly 3 lines; unattributed
  is called out on line 1 and as its own object in the JSON (the design asked
  for both "3 lines" and "its own line").
- **Archive:** plain CSV, `archive/<year>-apps.csv` + `<year>-totals.csv`
  (two files because there are two rollup tables). gzip was skipped: a year of
  rollups is ~1-2 MB of CSV, not worth a compression crate. Only whole years
  that ended > 5 years ago are exported (write-once files), verified by
  re-reading, deleted in the same transaction.
- **Class cache (`app_class`) is refreshed by `report`/`query`, not in the
  sample path:** the refresh scans every raw row (~260 ms); in `sample` it
  pushed the daily maintenance run to 5.1 MB RSS.
- **Failures** leave a single overwritten `last-error.txt` in the data dir
  (bounded) and a non-zero exit; success removes it.

**Follow-up (TIX-2611-037): error log, gap tracking, name trim.**
- **Capped error log.** launchd sends stdout/stderr to /dev/null, so every
  failure of `sample` (nettop spawn/timeout/exit/io, netinfo, DB, maintenance)
  and every `report`/`query` error is also appended as one line to
  `netmon.log` (`<UTC time> <subcommand> <kind> <message>`, 0600) in the data
  dir (`src/errlog.rs`). Open-append-close per event; message capped at 300
  chars with control characters flattened. Above 256 KB the file is renamed to
  `netmon.log.1` (replacing the old one), so the total is bounded at ~512 KB.
  Logging is best effort: its own failures are swallowed and it never creates
  the data dir. Stderr output is unchanged.
- **Skipped samples are recorded.** A failed `sample` still writes no partial
  data, but once the DB is open it inserts one row in `sample_failures`
  (`ts, day, kind, detail`; kind `timeout|exit|spawn|io|netinfo|db`) in its own
  tiny transaction. If even that fails it is logged, not fatal. The next good
  sample still covers the gap correctly (deltas are cumulative-counter
  differences); the row just makes the gap visible. Failure rows follow the
  35-day raw retention (deleted by the rollup; no rollup of their own).
- **Surfacing.** `report` JSON gets `gaps:{failed_samples, by_kind,
  longest_interval_s}`; the 3-line summary appends `, N FAILED (kind n, ..)` to
  line 1 only when N > 0. `query` JSON gets the same `gaps` object for the
  requested range (before `rows`); CSV stdout is untouched and a one-line
  notice goes to stderr when failures exist. `longest_interval_s` is the max
  `interval_s` of raw samples in range; it is `null` for days already rolled
  up (retention) or with no sample. Report/query `schema_version` stay 1: the
  change is purely additive.
- **Schema migrations.** Schema is now version 2 and applied as an ordered
  `MIGRATIONS` list (v1 = original, v2 = `sample_failures`), each step in one
  transaction with the `user_version` bump; a v1 file upgrades in place.
- **Name trim.** `nettop::parse_line` trims whitespace from process names
  (nettop pads some, e.g. `"Microsoft Edge "`), so storage, rules, reports and
  the `(name, pid)` delta key all use the clean name. All-space names are
  rejected. A DB written before this change that holds a padded name would see
  that process as new once (pid+name key changes); none is installed yet.
- **`netmon sample --dry`** prints parsed nettop rows without touching the DB.

**Maintenance headroom and helper timeout (TIX-2611-037).** The maintenance
run had crept to 5.0-5.2 MB (environment drift: the same binary measured 4.0 MB
days earlier), leaving no headroom. Measured by skipping the rollup: it alone
cost ~1.5 MB. `rollup_before` now walks the old days one at a time inside the
same single transaction (still all-or-nothing) and deletes `app_deltas` with one
indexed `sample_id = ?` delete per sample instead of `IN (subquery)`, which
avoided SQLite's sorter and ephemeral key set over the whole backlog: median
4.41 MB RSS (6 runs), CPU 100 -> 25 ms. `cache_size` went from -256 to -16
(about -0.2 MB; steady state unchanged). `temp_store`, `mmap_size` and
`cache_spill` made no measurable difference. The budget stays one 5 MB limit.
Helper timeout: each of netstat/sysctl/route/arp is capped at 5 s (was 3 s; a
loaded Mac can stall a fork+exec), route/arp lookups share a 10 s budget, so a
run is bounded at nettop 10 + 5 + 5 + 10 = 30 s of the 120 s interval. A netstat
timeout is a recorded `netinfo` failure; the others degrade to "unknown".
`NETMON_HELPER_TIMEOUT_MS` overrides the cap; the integration harness sets 60 s
so fake shell helpers can never flake under parallel load.

**nettop `-s` / wall time (measured, real nettop, this Mac).** `-s` does not
help with `-L 1`: wall time is a *cold-start* cost of nettop itself, not the
delay. Back-to-back runs take 0.17–0.25 s; the first run after ~40 s idle took
3.4–5.0 s (CPU 0.06–0.09 s, peak RSS 7.5–9 MB) regardless of `-s 1` or `-s 2`.
`-s 0` is invalid (exit 64). Settled on `-s 1` (lowest valid) and kept the 10 s
timeout (worst observed 5.0 s; two-minute cadence means every run is "cold").

**Budgets (release build, `cargo test --release --test budget -- --ignored`;
netmon's own `getrusage`, nettop excluded):**

| | measured | budget |
|---|---|---|
| steady-state sample, median CPU (user+sys) | ~7 ms (real nettop run: 7–14 ms) | < 50 ms |
| steady-state sample, peak RSS | 3.3 MB (real run 3.3–3.6 MB) | < 5 MB |
| daily maintenance run (rollup+archive check), 36-day DB | ~25 ms CPU, 4.4 MB RSS (median of 6) | stays < 5 MB |
| DB at 35 days (25 k samples, 1 M app rows) | 34 MB | < 50 MB |
| 1-year projection (35 d raw + 365 d worst-case rollups) | 37.7 MB | < 100 MB |
| release binary | 1.39 MB (stripped, `opt-level="z"`, LTO) | — |

`/usr/bin/time -l` folds waited-for children into its numbers (it would report
nettop's 7.5 MB as netmon's), hence the opt-in `NETMON_SELF_STATS=1` line
(`getrusage(RUSAGE_SELF)`, the crate's only `unsafe`).
