# Design: cross-account Claude Code usage stats + dashboard

- **Date:** 2026-08-22
- **Status:** approved design — not yet implemented
- **Origin:** an ad-hoc analysis of `~/.claude/history.jsonl` revealed (a) that file
  is stale since 2026-04-16 (the multi-account split moved live history into
  per-account `CLAUDE_CONFIG_DIR`s), and (b) 2933 history entries are a shared
  pre-split prefix duplicated across every account, so naive per-account counts
  are wrong. This setup makes the analysis reproducible instead of ad-hoc.

## Goal

One command — `claude-acs stats` — that walks every Claude Code account on the
machine, emits a **machine-readable `stats.json`**, and renders a **standalone
HTML dashboard** for tracking prompt volume, cadence, token consumption, model
mix, tool usage, and project distribution across accounts over time.

Non-goals (explicit YAGNI cuts):
- **No dollar-cost estimation.** Token counts only. A price table is a
  maintenance liability that goes stale silently and misreports confidently.
- **No raw prompt text in output.** Derived fields only (see "Privacy").
- **No background daemon, cron, or file watcher.** Manual invocation. Scheduling
  can be layered on later without touching the collector.
- **No chart library, no CDN, no build step.** Vanilla JS + inline SVG.
- **No session wall-clock duration metrics.** `sessionId` is reused across
  `--resume`; observed spans reach 977 hours. The number is meaningless (see "Correctness traps").

## Accounts in scope

Discovered at runtime, not hardcoded:
- `$CLAUDE_ACCOUNT_STORE/<account>/` — currently `harrison`, `personal`, `personal2`
- `~/.claude` — reported as the `canonical` pseudo-account (holds pre-split history)

All are in scope, including the inactive `personal2` — its transcripts are
unique even though its history is a duplicate (see "Dead accounts", below).

A directory counts as an account only if it contains `history.jsonl` **or** a
`projects/` subdirectory. This test exists because `$CLAUDE_ACCOUNT_STORE/bin/`
sits inside the store and would otherwise register as a phantom account.

## Architecture

Four parts, each with one job. Mirrors the `claude-code-account-profiles`
split: repo-as-truth, `claude-acs` as the ergonomic entry point only.

| Part | Role | Lives in |
|---|---|---|
| `collect_stats.py` | **Logic** — discovery, parsing, dedup, aggregation, render. stdlib Python 3, no deps. | repo |
| `dashboard/template.html` | **View** — self-contained page with a JSON injection point. | repo |
| `~/.cache/claude-acs-stats/` | **State** — parse cache + generated `stats.json` + rendered `dashboard.html`. | machine |
| `claude-acs stats` | **Entry** — thin wrapper that locates the repo and invokes the script. | `claude-acs` fn source |

Generated output and cache live outside the repo: the data is personal, the repo
is shared.

## Data model

### Layer A — prompts (`<account>/history.jsonl`)

Per line: `display`, `timestamp` (ms), `project`, `sessionId`, `pastedContents`.

**Dedup is mandatory.** Key on `(timestamp, display)`. Without it the 2933-entry
shared pre-split prefix is counted once per account and every account's history
falsely appears to begin 2026-02-17.

**Both views are reported — raw and deduplicated.** The duplication is itself a
finding worth seeing, not just an error to correct silently.

Per account, three figures:

| Figure | Definition |
|---|---|
| **total** | Every entry in that account's `history.jsonl`. Duplicates included. The raw view. |
| **exclusive** | Keys present in that account and in no other. Pure set difference. |
| **shared** | `total − exclusive` — the pre-split prefix bleeding in from other accounts. |

Globally: **union** (distinct keys across all accounts) alongside
**sum-of-totals** (the naive inflated figure). The dashboard carries a
`raw / deduped` toggle driving every panel, so both views are available without
re-running the collector.

All figures are set-theoretic and therefore order-independent — no "first-seen
wins" tie-break is needed, and none is defined. Accounts are still iterated in
sorted name order, for reproducible logs.

Reporting `shared` explicitly is what makes a dead account legible: `personal2`
reads total 2331 / exclusive 1 / shared 2330, which states "this is a stale
snapshot" more plainly than prose could.

### The shared prefix is frozen

The contamination predates the multi-account split and cannot grow: no new
duplicate can be created, because each account now writes to its own
`CLAUDE_CONFIG_DIR`. Two consequences the implementation should exploit:

1. **Invariant, assert it.** A given account's `shared` count must never increase
   between runs. If one does, either an account was restored from an old backup
   or the dedup key has broken — both real bugs, and this catches them for free.
2. **Fast path.** A `--since` date after the split needs no cross-account set
   math at all. Skip the dedup entirely for post-split windows.

### Dead accounts: per-layer, not per-account

"Dead" is a property of a *layer*, not an account. `personal2`'s `history.jsonl`
is a duplicate copy (1 exclusive prompt of 2331), but its ~949 transcripts are
unique session data with real token counts found nowhere else. So: dedup Layer A
aggressively, and treat Layer B as authoritative per file. Never exclude an
account from transcript parsing on the strength of its history being duplicated.


### Layer B — transcripts (`<account>/projects/**/*.jsonl`)

~5726 files / 2.0 GB at time of writing. Per-file rollup:

| Field | Source |
|---|---|
| turn counts | `type` in (`user`, `assistant`, `system`); `isSidechain` splits out subagent turns |
| per-model tokens | `message.model` → `message.usage.{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}` |
| tool usage | `message.content[].type == "tool_use"` → `.name` |
| context | `cwd`, `gitBranch`, `version` (sets, not counts) |
| time span | first / last `timestamp` |

### Incremental cache

`~/.cache/claude-acs-stats/cache.json`, keyed `(abspath, mtime_ns, size)` →
that file's rollup. Transcripts are append-only, so any new content changes
`size` and the key self-invalidates. A `schema_version` field forces a full
re-parse when the rollup shape changes.

Expected, measured on the real 1.99 GB / 5695-file corpus: **~14s cold**
(147 MB/s, 1.7 ms/file), and ~1s warm.

This is much faster than assumed when the cache was specified — the estimate was
"minutes cold". The cache is therefore a convenience, not a necessity: a cold run
is already interactive. Two consequences worth keeping in mind:

- `--no-cache` is a perfectly usable default if the cache ever misbehaves.
- The known wart that a filtered run (`--accounts`, `--since`) drops cache
  entries for skipped files costs ~13s on the next full run, not minutes. Not
  worth engineering around.

## Output

Two artifacts, because `file://` pages cannot `fetch()` a sibling JSON file:

1. `stats.json` — the ingestible artifact. Aggregates only, no raw prompt text.
2. `dashboard.html` — `template.html` with `stats.json` injected inline as
   `<script type="application/json">`, so it opens standalone with no server.

## `stats.json` schema

**Design principle: store a granular atom, derive panels client-side.** New
panels should be a dashboard-only change. If a new panel requires re-running the
collector over 2 GB of transcripts, the grain was chosen too coarse.

`schema_version` is an integer, bumped on any breaking shape change. The
dashboard refuses to render a version it does not recognise, rather than drawing
a plausible-looking wrong chart from missing keys.

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-08-22T00:14:00Z",
  "timezone": "Australia/Sydney",   // IANA name. ALL times below are UTC;
                                    // this is the collector's local zone, so a
                                    // consumer can convert exactly, DST included.
  "window": { "since": null, "until": "2026-08-22" },

  "run": {                          // freshness footer + trust signals
    "files_scanned": 5726,
    "files_parsed": 412,            // cache misses this run
    "cache_hits": 5314,
    "malformed_lines": 3,
    "duration_seconds": 8.4,
    "accounts_skipped": []
  },

  "accounts": [
    { "name": "harrison", "path": "~/.claude-accounts/harrison",
      "is_canonical": false,
      "prompts": { "total": 7527, "exclusive": 4594, "shared": 2933 },
      "prompt_chars": { "mean": 156, "median": 67, "p90": 387, "p99": 1292, "max": 5647 },
      "pasted_count": 271,
      "first_seen": "2026-02-17", "last_seen": "2026-08-21" }
  ],

  "totals": { "union": 8232, "sum_of_totals": 15595 },

  // ── Layer A atom: one row per (account, UTC date, UTC hour, project) ──
  // Row count is bounded above by the total prompt count, since every prompt
  // falls in exactly one bucket. Sparse in practice.
  "prompt_buckets": [
    { "a": "harrison", "d": "2026-08-11", "h": 4,
      "p": "~/repos/annalise-v2", "n": 12, "x": 12 }
    // a=account, d=UTC date, h=UTC hour, p=project, n=total, x=exclusive
  ],

  // ── Layer B atom: one row per (account, UTC date, model) ──
  "token_buckets": [
    { "a": "harrison", "d": "2026-08-11", "m": "claude-sonnet-5",
      "in": 4210, "out": 88134, "cr": 19407221, "cw": 210488,
      "turns": 606, "sidechain_turns": 42 }
    // cr=cache_read, cw=cache_creation
  ],

  // ── One row per session. See "Session rows" below on why the time fields
  //    are named as they are, and why duration is absent.
  "sessions": [
    { "a": "harrison", "id": "fb3eb53b-…", "p": "~/repos/orchestrator",
      "prompts": 12, "turns": 88, "sidechain_turns": 4,
      "first_ts": "2026-08-11T04:02:11Z", "last_ts": "2026-08-11T06:40:02Z",
      "resumed": false,             // true if the id appears across >1 calendar day
      "models": ["claude-sonnet-5"],
      "in": 4210, "out": 88134, "cr": 19407221, "cw": 210488,
      "branches": ["main"]
    }
  ],

  "session_histogram": { "harrison": { "1": 254, "2": 118 } },  // prompts→count

  "projects": [                     // convenience rollup, derivable from buckets
    { "a": "harrison", "path": "~/repos/annalise-v2", "n": 979,
      "first_seen": "2026-02-18", "last_seen": "2026-08-21" }
  ],

  "tools":    [ { "a": "harrison", "name": "Bash", "n": 4821 } ],
  "slash":    [ { "a": "harrison", "name": "/code-review", "n": 171 } ],
  "branches": [ { "a": "harrison", "name": "main", "n": 88 } ],
  "versions": [ { "a": "harrison", "name": "2.1.220", "n": 1079 } ]
}
```

### Why these grains

| Choice | Reason |
|---|---|
| `(account, date, hour, project)` for prompts | One atom, not two. Row count is bounded by the prompt total (8232 today), because each prompt lands in exactly one bucket — so adding the project dimension costs a few thousand rows, not a multiplicative blowup. Yields per-day *and* per-hour project analysis, plus every time-based panel, from a single array. |
| `(account, date, model)` for tokens | A few hundred rows. Token trends, model mix over time, cache-hit ratio. |
| Short keys in the two bucket arrays | These arrays dominate file size; long keys would roughly double it. Every other object uses readable keys. |
| One row per session | ~1.2k sessions from history, up to ~5.7k counting transcript files. Enables drill-down and calendar correlation. |
| Histogram kept alongside session rows | Derivable from the rows, but costs well under a kilobyte and saves the dashboard a full pass on load. |
| `projects` rollup kept | Also derivable; ~150 rows, and it carries first/last-seen without a scan. |

Expected size: roughly **1–1.5 MB**, up from the earlier 300–600 KB estimate —
per-day projects and per-session rows are what grew it. Still parsed by a browser
in milliseconds when inlined.

### Time is stored in UTC

Every timestamp, date, and hour in this file is **UTC**. The collector's local
zone is recorded once as an IANA name (`timezone`), so any consumer can convert
exactly, including across DST transitions — which a fixed numeric offset could
not do for a zone like `Australia/Sydney` (UTC+10/+11).

The dashboard converts to local for display; the file stays portable.

Caveat, stated honestly: bucketing at hour granularity in UTC is a lossless
round-trip only for zones whose offset is a whole number of hours. For a
half-hour or 45-minute zone (`Asia/Kolkata`, `Australia/Eucla`, `Pacific/Chatham`)
a UTC hour bucket straddles two local hours. `Australia/Sydney` is a whole-hour
zone, so this is exact here; the limitation is documented rather than engineered
away, because engineering around it means storing epoch-minute buckets and
tripling the row count for no local benefit.

### Session rows

`sessionId` is **reused across `--resume`**, so a session row is not a
contiguous work block. The observed maximum first-to-last span is 977 hours.
Therefore:

- The time fields are named `first_ts` / `last_ts`, never `start` / `end`.
- **No `duration` field exists**, and the dashboard must not compute one. A
  panel showing "session length" would be presenting resume artefacts as work
  sessions.
- A `resumed` boolean flags rows whose id spans more than one calendar day —
  the cheap signal that this row's span is not a sitting.

For calendar correlation, `prompt_buckets` is the honest source: it records when
prompts actually happened, at hour resolution, with no span inference.

### Stability contract

`stats.json` is a **versioned artifact**, not a private implementation detail:
additive changes keep the version, breaking changes bump it. This costs one
integer and makes the file safe for anything else to ingest later.

## Dashboard panels

An account filter drives every panel:

summary cards per account · calendar activity heatmap · prompts/day with 7-day
rolling mean · hour-of-day × weekday matrix · stacked token area over time
(input / output / cache-read / cache-write) · model mix over time · top tools ·
top projects · prompts-per-session distribution · slash-command leaderboard ·
freshness footer (generated-at, files parsed, cache hits, malformed lines skipped)

Vanilla JS and inline SVG throughout. No chart library: nothing to break when a
CDN dies or an API changes, and the page stays copyable to any machine.

## Correctness traps

These are the failure modes the implementation must actively defend against.
Each was observed in the real data, not hypothesised.

1. **Double-counted tokens.** `message.usage` carries an `iterations[]` array
   that repeats the same token figures. Read top-level `usage` fields only.
   `usage.cache_creation` is a second such structure: a nested dict whose
   values sum to `cache_creation_input_tokens`. Same defence.
2. **Repeated `message.id`.** Far more pervasive than resume/retry: **one
   assistant message is written as several JSONL lines, one per content block**
   (`thinking` / `text` / `tool_use`), all sharing `message.id` and each
   repeating the full usage figures. Measured: 688 assistant lines for 341
   unique ids (2.02x), 341/341 with identical usage across their lines.

   So accounting splits by grain: `tool_use` blocks count **per line** (each
   duplicate line holds a distinct block — deduping loses most tool calls),
   while turns, branch/version/project counts, and all token figures count
   **per message**. Invariant to hold: `turns` must equal
   `sum(by_day_model[*].turns)`.
3. **`bin/` inside the account store** — see "Accounts in scope".
4. **Reused `sessionId`.** Observed max first-to-last span: 977 hours, because
   `--resume` reuses the id. Report prompts-per-session; never duration.
5. **Cache-read tokens dwarf input tokens.** A single observed turn read 457410
   cached tokens against 2 input tokens. Any panel that stacks these on one
   linear axis renders the other series invisible — cache-read needs either its
   own axis or a log scale.
6. **Malformed lines.** Never fatal. Count them and surface the count on the
   dashboard, so silent parse failures cannot masquerade as low activity.
7. **Cross-account path leakage.** 26 personal-account prompts were issued
   against a work OneDrive path. The project panel must attribute by account, or
   this kind of leak stays invisible.

## Privacy

`stats.json` contains **no per-prompt row of any kind** — not the text, not a
length, not a hash. Every prompt is aggregated into a `(account, date, hour,
project)` bucket before anything is written. Prompt text and the
`(timestamp, display)` dedup key exist only in memory during a run.

This is stronger than merely omitting bodies, and it falls out of the schema
rather than being bolted on: since the grain is a bucket, there is no per-prompt
record to leak. An earlier draft proposed storing a truncated SHA-256 per prompt
so dedup could be audited from the file alone; that is dropped, because it would
have required adding an ~8k-row array that the schema otherwise has no need for.
Dedup is instead verified by the losslessness assertions in "Testing" — summing
bucket `n` must equal `sum_of_totals`.

Per-account prompt-length statistics (mean, median, p90, p99, max) are emitted as
aggregates on the `accounts` entries, since they are useful and carry no text.

Project paths **are** retained — they are required for the project
panel, and are already visible in the account directory names.

Consequence, accepted: no prompt search and no word-frequency panel in the
dashboard.

## Error handling

- Account dir missing, or missing `history.jsonl` → skip, note in output.
- Malformed JSON line → count, continue.
- Cache schema mismatch → discard cache, full re-parse.
- Missing repo script → wrapper prints a hint naming the override var, matching
  `apply-profile` behaviour.

### Repo location override

`$CLAUDE_ACS_REPO`, falling back to the existing `$CLAUDE_ACS_PROFILES_REPO`,
then to `$HOME/repos/acs-agentic-setup`. A new general-purpose name is
introduced because `..._PROFILES_REPO` is specific to the profiles setup and
would misdescribe this one; the fallback keeps a single existing override
working for both.

## CLI

```
claude-acs stats [--accounts a,b] [--since YYYY-MM-DD] [--out DIR]
                 [--no-cache] [--json-only] [--open]
```

`stats` has no two-letter alias: `st` is already `status`. `usage` is accepted
as a long alias.

## Testing

`scripts/test_collect_stats.py`, stdlib `unittest`, fixtures written to a
tmpdir. Cases: cross-account dedup; `total`/`exclusive`/`shared` consistency
(`shared == total - exclusive`, and `union <= sum-of-totals`); figures invariant
under account iteration order; cache invalidation on size change; `message.id`
dedup; `iterations[]` not double-counted; `bin/` exclusion; malformed-line
tolerance; account discovery with a missing `projects/` dir; a duplicate-history
account still contributing its transcript tokens.

Schema-specific cases added by the granular grains:
- `prompt_buckets` row count never exceeds the total prompt count, and summing
  `n` across all rows equals `sum_of_totals` (the bucket array must be lossless).
- `session_histogram` agrees with the `sessions` rows it is derived from — the
  convenience copy cannot drift from its source.
- `projects` rollup totals equal the same figures re-derived from
  `prompt_buckets` by summing over hour.
- All emitted dates/hours are UTC: a fixture prompt at a known epoch lands in the
  UTC bucket, not the local one.
- A session whose id spans >1 calendar day is flagged `resumed: true`, and no
  `duration` key is emitted anywhere in the output.

Run: `python3 -m unittest discover -s scripts`
