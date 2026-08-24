# Cross-account Claude Code usage stats

- **Created:** 2026-08-22
- **Status:** active
- **Agents involved:** Claude Code
- **Environment:** local
- **Topology:** single agent, multiple accounts

## What this is

One command — `claude-acs stats` — that walks every Claude Code account on the
machine, emits a versioned machine-readable `stats.json`, and renders a
standalone `dashboard.html` (13 panels, inline SVG, zero external requests) for
prompt volume, cadence, token consumption, model mix, tool usage and project
distribution across accounts over time.

Two data layers feed one aggregator:

- **Layer A — prompts:** `<account>/history.jsonl`.
- **Layer B — transcripts:** `<account>/projects/**/*.jsonl` (1.99 GB / 5706
  files at time of writing).

Full rationale, schema and correctness traps: [`design.md`](design.md). The
task-by-task build record: [`implementation-plan.md`](implementation-plan.md).

## Why

It started as an ad-hoc poke at `~/.claude/history.jsonl`, which surfaced two
things worth making reproducible:

1. **That file is stale.** It stopped growing on 2026-04-16, when the
   multi-account split moved live history into per-account `CLAUDE_CONFIG_DIR`s.
   Any analysis reading only `~/.claude` sees a four-month-old machine.
2. **2933 history entries are a shared pre-split prefix, duplicated across every
   account.** So naive per-account counts are simply wrong, and every account
   falsely appears to begin on 2026-02-17.

Answering "how much am I actually using Claude Code, and where?" therefore takes
cross-account set math, not a `wc -l`. This setup makes that analysis a command
instead of an afternoon.

## Components

- **`scripts/`** — six focused stdlib-only modules behind a thin CLI:
  `discovery.py` (find accounts), `history.py` (Layer A + dedup + buckets),
  `transcripts.py` (Layer B per-file rollup), `cache.py` (incremental parse
  cache), `aggregate.py` (owns the schema), `render.py` (JSON → HTML),
  `collect_stats.py` (CLI).
- **`dashboard/template.html`** — the view, with a JSON injection point.
- **`claude-acs stats`** — wrapper in `~/claude_code_toggle.sh` (not tracked
  here; it lives in `$HOME`).

## Reproducing it

```bash
# Everything: stats.json + dashboard.html into ~/.cache/claude-acs-stats/
claude-acs stats

# `usage` is a long alias. There is no two-letter alias -- `st` is `status`.
claude-acs usage

# Direct invocation (no claude-acs):
python3 scripts/collect_stats.py --store ~/.claude-accounts
```

Point the wrapper at a non-default checkout with
`export CLAUDE_ACS_REPO=/path/to/acs-agentic-setup`. Resolution order is
`$CLAUDE_ACS_REPO` → `$CLAUDE_ACS_PROFILES_REPO` (the older name, kept working
for both setups) → `$HOME/repos/acs-agentic-setup`.

### Flags

| Flag | Effect |
|---|---|
| `--store DIR` | Account store to scan. Default `~/.claude-accounts`. `claude-acs` passes `$CLAUDE_ACCOUNT_STORE`. |
| `--canonical DIR` | Config dir reported as the `canonical` pseudo-account. Default `~/.claude`. |
| `--accounts a,b` | Restrict to named accounts. Also shrinks the cache — see *Notes*. |
| `--since YYYY-MM-DD` | Earliest **UTC** date to include. |
| `--until YYYY-MM-DD` | Latest **UTC** date to include. |
| `--out DIR` | Output dir. Default `~/.cache/claude-acs-stats`. |
| `--no-cache` | Ignore *and* do not write the parse cache. A cold run is ~11s, so this is a perfectly usable default if the cache ever misbehaves. |
| `--json-only` | Skip `dashboard.html`; write `stats.json` only. |
| `--open` | Open the rendered dashboard when done. |
| `--quiet` | Suppress progress output. |

## Output

Everything lands in `~/.cache/claude-acs-stats/` — **deliberately outside the
repo**: the data is personal, the repo is shared. Nothing generated is ever
committed.

| Artifact | Size (full corpus) | Note |
|---|---|---|
| `stats.json` | 2.30 MB | The ingestible artifact. `schema_version: 1`, `indent=1` so it stays human-diffable. |
| `dashboard.html` | 2.34 MB | `template.html` (43 KB) with `stats.json` inlined as `<script type="application/json">` — a `file://` page cannot `fetch()` a sibling. |
| `cache.json` | 5.63 MB | Larger than both outputs combined. **Safe to delete at any time**; a cold rebuild is ~11s. |

## The three-figure dedup model

Per account, prompts are reported three ways rather than being silently
corrected to one:

| Figure | Definition |
|---|---|
| **total** | Every entry in that account's `history.jsonl`, duplicates included. The raw view. |
| **exclusive** | Keys — `(timestamp, display)` — present in that account and in no other. A pure set difference. |
| **shared** | `total − exclusive`. The pre-split prefix bleeding in from other accounts. |

As of the 2026-08-21 snapshot:

| Account | total | exclusive | shared |
|---|---|---|---|
| `harrison` | 7527 | 4594 | 2933 |
| `personal` | 3500 | 567 | 2933 |
| `personal2` | 2331 | 1 | 2330 |
| `canonical` | 2237 | 137 | 2100 |

Globally: **union 8232** distinct prompts against a **sum-of-totals of 15595** —
the naive inflated figure.

**`total` and `exclusive` grow as you work** (running the build for this setup
appended to `history.jsonl` mid-flight). `shared` does not: the contamination
predates the multi-account split and cannot grow, because each account now writes
to its own `CLAUDE_CONFIG_DIR`. So those four `shared` figures — 2933 / 2933 /
2330 / 2100 — are the frozen, invariant part, and a useful self-check: if one
ever moves, either an account was restored from an old backup or the dedup key
has broken. Both are real bugs.

**Why both views ship, instead of just the deduped one:** the duplication is
itself a finding. `personal2` reading total 2331 / exclusive 1 / shared 2330
states "this is a stale snapshot" more plainly than any prose could. The
dashboard carries a `raw / deduped` toggle driving every panel, so both views are
available without re-running the collector.

All figures are set-theoretic and therefore order-independent — no "first-seen
wins" tie-break exists or is needed.

### "Dead" is a property of a layer, not an account

`personal2`'s `history.jsonl` is a duplicate copy (1 exclusive prompt of 2331),
but its ~950 transcripts are unique session data with real token counts found
nowhere else. So Layer A is deduped aggressively while Layer B is treated as
authoritative per file. **No account is ever excluded from transcript parsing on
the strength of its history being duplicated.**

## Time is stored in UTC

Every timestamp, date and hour in `stats.json` is **UTC**. The collector's local
zone is recorded once, as an IANA name (`"timezone": "Australia/Sydney"`), so a
consumer can convert exactly — including across DST transitions, which a fixed
numeric offset could not do. The dashboard converts to local for display; the
file stays portable.

## Privacy: no per-prompt data is ever written

`stats.json` contains **no per-prompt row of any kind** — not the text, not a
length, not a hash. Every prompt is aggregated into an
`(account, UTC date, UTC hour, project)` bucket before anything is written.
Prompt text and the `(timestamp, display)` dedup key exist only in memory during
a run.

This falls out of the schema rather than being bolted onto it: since the grain
*is* a bucket, there is no per-prompt record to leak. Accepted consequence — no
prompt search and no word-frequency panel. Per-account prompt-length aggregates
(mean/median/p90/p99/max) *are* emitted, since they carry no text. Project paths
are retained, because the project panel needs them and they are already visible
in the account directory names.

## Performance

Measured end to end on the real 1.99 GB / 5706-file corpus:

- **Cold: 11.3s.**
- **Warm: 0.71s**, with 5703/5706 cache hits. The three misses are live session
  files that grew between runs — the append-only invalidation working as designed.

The cache (`~/.cache/claude-acs-stats/cache.json`) is keyed on
`(abspath, mtime_ns, size)`. Transcripts are append-only, so any new content
changes `size` and the key self-invalidates. A rollup-schema bump discards the
whole cache.

## Config

- No secrets, no network, no third-party dependencies. **Python 3.9.6 stdlib
  only** (`/usr/bin/python3` on this machine), matching the `apply_profile.py`
  precedent next door.
- `stats.json` is a versioned artifact, not a private implementation detail:
  additive changes keep `schema_version`, breaking changes bump it. The dashboard
  refuses to render an unrecognised version rather than drawing a
  plausible-looking wrong chart from missing keys.

## Notes / known issues

- **Filtered runs shrink the cache.** A run with `--accounts` or `--since` writes
  back only the keys it touched, so entries for skipped files age out and the
  next full run re-parses them. That costs ~11s, not minutes — not worth
  engineering around.
- **No session duration, anywhere.** `sessionId` is reused across `--resume`;
  the observed maximum first-to-last span is 977 hours. The time fields are named
  `first_ts` / `last_ts`, no `duration` key is emitted, and the dashboard must
  never compute one. A `resumed` boolean flags rows spanning >1 calendar day.
  For calendar correlation, `prompt_buckets` is the honest source.
- **No dollar-cost estimation.** Token counts only. A price table goes stale
  silently and then misreports confidently.
- **Cache-read tokens dwarf input tokens** — one observed turn read 457410 cached
  tokens against 2 input. Any panel stacking these on one linear axis renders the
  other series invisible, so the token panels separate them.
- **Malformed JSON lines are never fatal.** They are counted and surfaced in the
  dashboard's freshness footer, so silent parse failures cannot masquerade as low
  activity.
- **UTC hour buckets are lossless only for whole-hour zones.** For
  `Asia/Kolkata`, `Australia/Eucla` or `Pacific/Chatham` a UTC hour straddles two
  local hours. `Australia/Sydney` is a whole-hour zone, so this is exact here;
  documented rather than engineered away, since the fix means epoch-minute
  buckets and triple the rows for no local benefit.
- **Worktree layouts resolve automatically.** If `~/repos/acs-agentic-setup` is
  a bare repo + worktrees layout, its root holds no `setups/` dir. The wrapper
  handles this: `_claude_acs_setup_script` tries `$repo/setups/<rel>` first, then
  scans `$repo/*/setups/<rel>` across worktrees, taking the first match in shell
  glob order. `CLAUDE_ACS_REPO` still overrides everything if you want a specific
  worktree. The same helper now backs `apply-profile`, which had been silently
  broken by the bare-repo conversion until this setup exposed it.

  Consequence to know: when a setup exists on more than one worktree, you get
  whichever sorts first — not the checked-out branch. Set `CLAUDE_ACS_REPO` when
  that matters.
- **The `<synthetic>` model** appears as a real `message.model` value carrying
  all-zero usage, so it produces a legitimate zero-token bucket row. It is kept
  in the data and filtered out of the dashboard's model-mix charts.
- Tests: `cd scripts && python3 -m unittest discover -s . -v` (49 tests). There
  is no `scripts/__init__.py`, so the dotted `scripts.test_x` form does not work
  — run from inside `scripts/`.
