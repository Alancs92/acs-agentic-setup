# claude-skills-backup

Managed, versioned, deduplicated cloud backup for hand-authored Claude
configuration — skills, commands, agents, hooks, per-account settings, and
plugin manifests.

> **This is skill/config backup.** It is unrelated to `claude-acs save`, which
> saves an *account profile*. Similar-sounding verbs, different subsystems.

```
claude-acs backup run
```

---

## LEGEND

Read this before the code. Every term below means exactly one thing throughout
this setup.

| Term | Meaning |
|---|---|
| **unit** | One backed-up thing, identified by `(kind, name)` — a skill, a hook, one settings file. Every source is normalised to units so there is a single pipeline rather than one per source type. |
| **kind** | Which source a unit came from: `skill`, `command`, `agent`, `hook`, `settings`, `claude_md`, `plugin_manifest`. |
| **snapshot** | A record of one *content change* to a unit. A unit that hasn't changed produces **no** snapshot — that is the point of hashing. |
| **content hash** | `sha256` over the unit's sorted file manifest. Deliberately excludes timestamps, so a `touch` or a OneDrive resync does not fabricate a snapshot. |
| **blob** | The `tar.xz` archive holding a snapshot's bytes, named by content hash. Identical content is stored exactly once, even across different units. |
| **blob key** | `objects/<ab>/<cd>/<sha256>.tar.xz` — the sharded path of a blob. |
| **tombstone** | A snapshot row whose blob has been pruned. `pruned_utc` is set, the row survives. You can still see that a version existed on a date, even though its bytes are gone. |
| **prune** | Delete a blob per the retention policy. Deletes bytes, never the record. |
| **thin_to** | Retention setting: collapse an age band to one snapshot per `weekly` or `monthly` bucket, keeping the newest in each. |
| **stable** | A unit whose *newest* snapshot is older than `stable_after_days` — it has settled, so its history collapses to the newest `stable_keep`. |
| **degraded** | A run that completed but wrote blobs to the local fallback because OneDrive was unavailable. Not a failure. |
| **live mirror** | The flat, browsable `OneDrive/Claude User Skills/` copy of current skills. A *replica*, not a backup — kept because it's readable in a browser. |

---

## Why this exists

`~/.claude/skills` is already a git repo (`acs-claude-skills`), so why back it up?

- **Git cannot forget.** Age-based retention is the one job Git structurally
  can't do, and unbounded history in a cloud folder is not free.
- **The working tree drifts from the commits.** Uncommitted edits are exactly
  what's lost in a disaster, and they're invisible to Git's history.
- **A flat mirror is a replica, not a backup.** It faithfully propagates
  deletion and corruption. Point-in-time restore needs versions.

This layer does disaster recovery and point-in-time restore. It does **not**
take ownership of skill history — `acs-claude-skills` keeps that.

## How a run works

```
discovery  enumerate units, resolve symlinks to real content
redact     scrub secrets from settings BEFORE anything is written
hashing    canonical content hash per unit
index      compare to newest snapshot; unchanged units write nothing
store      write tar.xz blob, content-addressed, deduped by hash
retention  select prunable snapshots, unlink unreferenced blobs
sync       refresh live mirror, place blobs + index.db in OneDrive
catalog    regenerate catalog.jsonl for commit
```

## Where things live

```
OneDrive - harrison.ai/
  Claude User Skills/                    # live mirror, browsable
  Claude Skills Backup/
    objects/<ab>/<cd>/<sha256>.tar.xz    # blobs (authoritative)
    index.db

this repo/setups/claude-skills-backup/
  config.json                            # committed — all tunables
  catalog.jsonl                          # committed — human-diffable audit trail
  scripts/                               # committed

~/.cache/claude-acs-backup/
  index.db  objects/                     # fallback when OneDrive is away
```

Blobs are **not** committed to Git: anything committed can never be pruned, which
would make the retention policy cosmetic. `catalog.jsonl` carries the audit trail
into Git instead, one sorted line per snapshot, so `git diff` reads as plain
English. `index.db` is rebuildable from the catalog plus the blobs.

## Commands

```
claude-acs backup run                # snapshot changed units, prune, sync
claude-acs backup status             # last run, store size, drift
claude-acs backup list [<unit>]      # snapshot history, tombstones included
claude-acs backup restore <unit> [--at <date>] [--to <dir>]
claude-acs backup prune [--dry-run]  # apply retention only
claude-acs backup verify             # hash-check blobs, reconcile orphans
claude-acs backup install-schedule   # generate + load the launchd job
```

`--dry-run` on `prune` prints exactly what would be deleted and reclaimed
without touching anything. Use it after changing retention settings.

## Configuration

Everything tunable lives in `config.json`. Cadence has a single source of truth
there — `install-schedule` regenerates the launchd job from it.

Retention, as configured:

| Age of snapshot | Kept |
|---|---|
| Newest 2 | Always, unconditionally |
| 0–30 days | All |
| 30–90 days | One per ISO week |
| 90+ days | One per calendar month |
| Unit unchanged 180+ days | Collapses to newest 2 |
| Unit has one snapshot | Never pruned |

`plugins.mode` is `"manifests"` by default: `~/.claude/plugins` is ~509MB, of
which 433MB is refetchable `cache/` and 75MB is `marketplaces/`. The ~430KB of
manifests is what actually reconstructs the setup.

`"full"` means **literally full** — it walks `cache/` and `marketplaces/` too, so
expect roughly 509MB per changed snapshot heading into OneDrive. It exists as a
deliberate escape hatch, not a recommendation.

One wart worth knowing: `plugin-catalog-cache.json` (~406KB) is captured in
`manifests` mode because it is a top-level `*.json`, despite being a cache. It
changes on its own schedule, so it will snapshot more often than the things you
actually authored. Add it to `exclude_globs` if the churn bothers you.

## Secrets

`settings.json` can carry tokens and these blobs land in **corporate OneDrive**.
Redaction runs before any byte enters an archive: values are replaced with
`«REDACTED:<hash prefix>»`, which preserves change detection without carrying the
secret. Set `secrets.mode` to `"skip"` to exclude settings entirely.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| OneDrive missing or unwritable | Blobs to local fallback, run marked `degraded`, reconciled next run |
| Run interrupted mid-write | Blobs orphaned but uncommitted; next `verify` reconciles |
| A source path unreadable | Skip that unit, record it, continue |
| Corrupt blob | `verify` detects the hash mismatch and re-snapshots |

The cloud being offline never aborts a backup.

## Scope note

Three repos currently hold pieces of the skill tree: `acs-claude-skills` (the
85 live), `claude-bootstrap` (13, symlinked in), and a stale
`claude-bootstrap/skills` (19, unused). This backup follows symlinks and captures
everything by content, so nothing is missed. **Consolidating those repos is a
separate change** and is deliberately not attempted here.

## Design

Full rationale, schema, and the retention algorithm:
`docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md`.
Frozen module signatures: `scripts/contracts.md`.
