# Design: netmon — per-device daily network usage by app

- **Date:** 2026-11-25
- **Status:** approved design (Alan, 2026-11-25) — not yet implemented
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
- Hard resource budgets, enforced by a test (below).

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
- **Reboot** (`kern.boottime` changed): all process counters restarted; drop
  prior per-process state and treat all as new.
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

### Budgets — enforced by a test

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
