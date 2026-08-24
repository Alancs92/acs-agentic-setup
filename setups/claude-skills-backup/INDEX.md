# setups/claude-skills-backup/

Managed, versioned, deduplicated cloud backup for hand-authored Claude
configuration. One command — `claude-acs backup` — hashes every skill, command,
hook, settings file and plugin manifest, snapshots only what changed, prunes on
an age-tiered retention policy, and places the blobs in OneDrive. Source of truth
for policy is `config.json`; the human-readable audit trail is `catalog.jsonl`
(committed); the blobs themselves deliberately live outside the repo, because
anything committed to Git can never be pruned.

Distinct from `claude-acs save`, which saves an *account profile*. Different
subsystem, similar-sounding verb.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | The setup: LEGEND of every term, why it exists alongside Git, run pipeline, commands, retention table, secrets, failure behaviour. |
| [`config.json`](config.json) | All tunables: sources, exclusions, storage roots, retention policy, secret patterns, schedule cadence. |
| [`catalog.jsonl`](catalog.jsonl) | Committed audit trail — one sorted line per snapshot, so `git diff` shows what changed and what was pruned. |
| [`scripts/contracts.md`](scripts/contracts.md) | Frozen module signatures. Read before changing any module; a mismatch breaks integration silently. |
| [`scripts/`](scripts) | `discovery`/`hashing`/`redact`/`index`/`store`/`retention`/`sync` behind the `backup.py` CLI, plus tests. |

Design and rationale: [`../../docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md`](../../docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md).

Related: [`../claude-code-usage-stats/`](../claude-code-usage-stats/INDEX.md) and
[`../claude-code-account-profiles/`](../claude-code-account-profiles/INDEX.md),
whose `claude-acs` wrapper split (repo-as-truth, shell fn as ergonomic entry)
this setup mirrors.

**Scope note:** three repos currently hold pieces of the skill tree —
`acs-claude-skills` (85 live), `claude-bootstrap` (13, symlinked in), and a stale
`claude-bootstrap/skills` (19, unused). This setup follows symlinks and captures
by content, so nothing is missed. Consolidating those repos is a separate change.
