# Claude Skills Backup — Design

**Date:** 2026-08-25
**Status:** Approved, in implementation
**Branch:** `feat/claude-skills-backup`

## Problem

Claude configuration authored by hand — skills, commands, hooks, settings — has
no managed backup. What exists today is:

| Location | Contents | Problem |
|---|---|---|
| `~/.claude/skills` | 85 skills, git repo `acs-claude-skills` | Working tree dirty; git can't prune |
| `~/repos/claude-bootstrap/.claude/skills` | 13 skills, symlinked into the above | Second repo owns part of the history |
| `~/repos/claude-bootstrap/skills` | 19 skills, unused | Stale, overlapping names |
| `OneDrive/Claude User Skills/` | 70 dirs, flat | Hand-run mirror, 15 skills stale, unversioned |
| 5 scattered `*backup*` dirs + loose zips | ad-hoc snapshots | No dedup, no retention, no index |

A flat mirror is a replica, not a backup: it propagates deletion and corruption
faithfully. Nothing today answers "give me this skill as it was six weeks ago".

## Non-goals

- **Not a version control system.** `acs-claude-skills` remains the source of
  truth and keeps ownership of skill history. This system is disaster recovery
  and point-in-time restore, layered on top.
- **Not a consolidation change.** Absorbing the 13 bootstrap skills, retiring
  the stale 19, and committing the dirty `turnstile-spin` tree are deferred to a
  separate change.
- **Not a sync tool.** Accounts already share one real directory through
  symlinks; there is nothing to reconcile between them.

## Constraints

- Python 3, **standard library only** — matches every other `claude-acs` setup.
- Blobs live in OneDrive, not Git. Git holds the engine, the config, and a
  human-diffable audit trail.
- Cadence and policy configurable from a single JSON file.
- Scripts must be legible to agents: module docstrings, a LEGEND in the README,
  comment-annotated SQL.

## Architecture

### The unit abstraction

Everything backed up is a **unit**, keyed `(kind, name)`. One uniform pipeline,
no per-source special cases.

| Kind | Source | Notes |
|---|---|---|
| `skill` | `~/.claude/skills/<name>` | Symlinks resolved to real content |
| `command` | `~/.claude/commands/<name>` | |
| `agent` | `~/.claude/agents/<name>` | Currently empty |
| `hook` | `~/.claude/hooks/<name>` | |
| `settings` | `~/.claude-accounts/<acct>/settings*.json` | Redacted before storage |
| `claude_md` | `~/.claude-accounts/<acct>/CLAUDE.md` | |
| `plugin_manifest` | `~/.claude/plugins/*.json` | Manifests only — see below |

**Plugins are manifests only.** `~/.claude/plugins` is 509M, of which 433M is
`cache/` and 75M is `marketplaces/` — both refetchable from source. The ~430K of
manifests (`installed_plugins.json`, `known_marketplaces.json`, `blocklist.json`,
`plugin-catalog-cache.json`) is what actually reconstructs the setup.
`config.json` exposes `plugins.mode` (`"manifests"` default, `"full"` override).

### Pipeline

```
discovery  -> enumerate units, resolve symlinks to real paths
redact     -> scrub secrets from settings before anything is written
hashing    -> canonical content hash per unit
index      -> compare against newest snapshot; unchanged units write nothing
store      -> write tar.xz blob, content-addressed, deduped by hash
retention  -> select prunable snapshots, unlink unreferenced blobs
sync       -> refresh live mirror, place blobs + index.db in OneDrive
catalog    -> regenerate catalog.jsonl for commit
```

### Hashing

Canonical hash is `sha256` over sorted tuples of
`(relative_path, is_executable, sha256(file_bytes))`.

**Timestamps are deliberately excluded.** Including mtime would make a `touch`,
a `git checkout`, or a OneDrive resync fabricate a snapshot on every cycle —
defeating the entire "don't waste space on unchanged skills" requirement.

### Storage layout

```
OneDrive - harrison.ai/
  Claude User Skills/                    # live mirror, refreshed to all 85
  Claude Skills Backup/
    objects/<ab>/<cd>/<sha256>.tar.xz    # authoritative blob home
    index.db

acs-agentic-setup/setups/claude-skills-backup/
    config.json                          # committed
    catalog.jsonl                        # committed audit trail
    scripts/                             # committed

~/.cache/claude-acs-backup/
    index.db  objects/                   # fallback when OneDrive is absent
```

The flat `Claude User Skills/` mirror is kept and auto-refreshed — it serves a
different purpose from the versioned store (browsable current state vs. point-in-
time recovery). Two folders, two jobs, no ambiguity.

### Why `catalog.jsonl` and not a committed `index.db`

A binary SQLite file in Git diffs as "binary file changed" and appends a full
copy per run. `catalog.jsonl` is one sorted line per snapshot, so `git diff`
reads as plain English:

```jsonl
{"kind":"skill","name":"deep-research","hash":"3f9a2c…","created":"2026-08-25T02:00Z","bytes":48211,"files":12,"pruned":null}
```

`index.db` is rebuildable from `catalog.jsonl` plus the blobs, so nothing is lost
by keeping it out of Git.

### Do not rename `backup_types.py`

The shared vocabulary module is `backup_types.py`, **not** `types.py`, and this is
load-bearing rather than stylistic.

A `types.py` in the scripts directory shadows the standard library's `types` for
any process whose `sys.path[0]` is that directory — which is every `python3
backup.py`, every `python3 -m unittest`, and every direct test run. CPython's own
`functools`, `enum` and `importlib` do `from types import GenericAlias`, hit the
local file instead, and the interpreter dies during startup before a single line
of project code executes.

Verified independently by two agents: fatal on Python 3.9.6 and 3.12
(`ImportError: cannot import name 'MappingProxyType' from partially initialized
module`); on 3.13 the stdlib wins instead and the shared module becomes
unreachable by name. There is no Python version where the name works.

## Schema

```sql
-- A unit is any single backed-up thing, identified by (kind, name).
CREATE TABLE units (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,   -- skill|command|agent|hook|settings|claude_md|plugin_manifest
  name           TEXT NOT NULL,
  source_path    TEXT NOT NULL,   -- resolved real path, symlinks followed
  first_seen_utc TEXT NOT NULL,
  last_seen_utc  TEXT NOT NULL,   -- bumped every run, even when unchanged
  deleted_utc    TEXT,            -- set when the unit vanishes from source
  UNIQUE(kind, name)
);

-- One row per *content change*. Unchanged units create no rows.
CREATE TABLE snapshots (
  id           INTEGER PRIMARY KEY,
  unit_id      INTEGER NOT NULL REFERENCES units(id),
  content_hash TEXT NOT NULL,     -- sha256 of the canonical manifest
  blob_key     TEXT NOT NULL,     -- objects/ab/cd/<sha256>.tar.xz
  size_bytes   INTEGER NOT NULL,
  file_count   INTEGER NOT NULL,
  created_utc  TEXT NOT NULL,
  run_id       INTEGER NOT NULL REFERENCES runs(id),
  pruned_utc   TEXT               -- tombstone: row survives, blob deleted
);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  started_utc TEXT NOT NULL, finished_utc TEXT,
  units_scanned INTEGER, snapshots_created INTEGER, blobs_pruned INTEGER,
  bytes_written INTEGER, bytes_reclaimed INTEGER,
  status TEXT,                    -- ok|degraded|failed
  error TEXT
);

CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
```

Two schema decisions carry weight:

**`pruned_utc` tombstones.** When retention deletes a blob the row remains, so
`claude-acs backup list <skill>` still reports that a version existed on a given
date even though the bytes are gone. Costs bytes per row; stops pruning from
erasing the record of what happened.

**`last_seen_utc` separate from snapshots.** This is what makes "no change means
no snapshot" safe — without it, "unchanged for six months" is indistinguishable
from "deleted six months ago", and that distinction is exactly what the
stability-collapse rule keys on.

## Retention

```json
{
  "retention": {
    "always_keep_latest": 2,
    "keep_all_within_days": 30,
    "tiers": [
      {"older_than_days": 30, "thin_to": "weekly"},
      {"older_than_days": 90, "thin_to": "monthly"}
    ],
    "stable_after_days": 180,
    "stable_keep": 2,
    "never_delete_only_copy": true
  }
}
```

Evaluated per unit, newest-first. **Order is significant** — stability collapse
comes before tier thinning and short-circuits:

1. The newest `always_keep_latest` snapshots are always kept, overriding all below.
2. `never_delete_only_copy` is a **floor**: a unit always retains at least one
   live snapshot, whatever the other settings say. Not a count-of-one check —
   `always_keep_latest: 0` with `stable_keep: 0` still leaves the newest one.
   This is the last line of defence against a hand-edited config destroying
   history that exists nowhere else.
3. **Stability collapse.** If a unit's *newest* snapshot is older than
   `stable_after_days`, the skill has settled — keep only the newest
   `stable_keep` and stop. Remaining rules are skipped.
4. Snapshots within `keep_all_within_days` are all kept.
5. Past 30 days keep one per ISO week; past 90 days one per calendar month,
   keeping the newest in each bucket. Tiers are matched
   highest-threshold-first, so config array order is not significant.
6. Anything not kept above is pruned.

Boundary semantics are deliberately asymmetric so the age line partitions with
no gap: `age < keep_all_within_days` is strict, `age >= older_than_days` is
inclusive, `age > stable_after_days` is strict. Making all three strict would
leave a snapshot at *exactly* 30 days matching neither rule 4 nor rule 5, and it
would fall through to rule 6 and be silently deleted. So exactly-30-days lands in
the weekly tier, and exactly-180-days does not collapse.

Omitted policy keys fail safe: absent `keep_all_within_days`/`stable_after_days`
read as infinite, absent `always_keep_latest` as 1, absent
`never_delete_only_copy` as true. A truncated config therefore prunes nothing
rather than wiping history. An unknown `thin_to` keeps everything in that tier —
a config typo costs disk, never data.

A blob is unlinked only when **no** live snapshot row references its hash —
dedup means two units can legitimately share one blob.

## Secret handling

`settings.json` can carry tokens, and blobs land in corporate OneDrive.
`redact.py` runs **before** any blob is written. It scans JSON values for
`sk-ant-` prefixes, bearer tokens, and keys matching
`token|secret|password|apiKey|credential`, replacing each value with
`«REDACTED:<sha256-prefix>»`. The hash prefix preserves change detection without
carrying the secret. Default mode `redact`; `"skip"` excludes settings entirely.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| OneDrive missing/unwritable | Write to `~/.cache/claude-acs-backup/`, mark run `degraded`, reconcile next run |
| Run interrupted mid-write | Blobs orphaned but uncommitted; next `verify` reconciles |
| Source path unreadable | Skip that unit, record it, continue — never abort the whole run |
| Corrupt existing blob | `verify` detects hash mismatch and re-snapshots |

The cloud being offline never aborts a backup.

## CLI

Registered as `claude-acs backup` — a thin wrapper following the existing
`_claude_acs_stats` pattern, resolving the script through
`_claude_acs_setup_script`. No conflict existed: `backup` was unused.

> **Note for future readers:** `claude-acs save` is *account-profile* save and is
> unrelated to `claude-acs backup`, which is skill/config backup. Different
> subsystems, similar-sounding verbs.

```
claude-acs backup run               # snapshot changed units, prune, sync
claude-acs backup status            # last run summary, store size, drift
claude-acs backup list [<unit>]     # snapshot history, tombstones included
claude-acs backup restore <unit> [--at <date>] [--to <dir>]
claude-acs backup prune [--dry-run] # apply retention only
claude-acs backup verify            # hash-check blobs, reconcile orphans
claude-acs backup install-schedule  # generate + load the launchd plist
```

## Scheduling

`com.acs.claude-skills-backup.plist`, matching the existing
`com.acs.tide-sync.plist` convention. Generated by `install-schedule` from
`config.json`'s `schedule.interval_days` (default 7), so cadence has exactly one
source of truth.

## Testing

TDD with stdlib `unittest`, mirroring `test_aggregate.py`. Priority is the code
where bugs are silent and destructive:

- Hash canonicalization is insensitive to mtime and path ordering
- Retention selection, table-driven: tier boundaries, stability collapse,
  only-copy guard, always-keep-latest override
- Blob refcounting: shared blob is not unlinked while any unit references it
- Redaction: secrets removed, change detection preserved
- Round trip: `run -> prune -> restore` on a temp tree reproduces content exactly
- Degraded mode: OneDrive absent still produces a valid local backup
