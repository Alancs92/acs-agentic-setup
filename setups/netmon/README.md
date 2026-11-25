# netmon — per-device daily network usage, by app

- **Created:** 2026-11-25
- **Status:** built and tested (2026-11-25); not yet installed (launchd install awaits Alan's approval)
- **Agents involved:** Claude Code (builder); other agents read its daily JSON
- **Environment:** local (this Mac only, for now)
- **Ticket:** TIX-2611-037

## What this is

A tiny Rust run-and-exit binary, fired by launchd every 2 minutes, that samples
`nettop` per-process byte counters, stores deltas in a local SQLite DB, and
produces a daily report: GB used, split home vs elsewhere, and which apps used
it, classified work / personal / ask.

## Why

Alan wants daily GB consumption and which apps used it, mapped work vs
non-work, for the end-of-day debrief. It measures on the device (so it is
network-independent) and tags the network. Hard requirement: very memory
efficient, no leaks, must not bog down the machine.

## Docs

- [`design.md`](design.md) — approved design, approaches, builder check.
- [`implementation-plan.md`](implementation-plan.md) — TDD task list.
- [`SCHEMA.md`](SCHEMA.md) — tables, SQL views and units, for agents reading the data.

## Use

```
cargo build --release
netmon sample [--dry]                 # one sample (launchd runs this every 2 min)
netmon report [--date YYYY-MM-DD]     # reports/<date>.json + 3-line summary
netmon query --from D --to D --group-by day|app|class|network[,..] --format json|csv
scripts/install.sh --dry-run          # shows what installing would do (install is a separate approval)
```

Tests: `cargo test`; release budgets (RSS, CPU, DB size, 1-year projection):
`cargo test --release --test budget -- --ignored --nocapture`.
