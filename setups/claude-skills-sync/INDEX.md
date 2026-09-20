# setups/claude-skills-sync/

Daily, pull-only sync that keeps `~/.claude/skills` current with the
`acs-claude-skills` remote and **cannot** lose local work. One command —
`claude-acs skills` — fetches, and fast-forwards only when the working tree is
completely clean and strictly behind. A dirty tree is skipped and logged, not
resolved; diverged history is skipped loudly and never merged, rebased or
reset. The target directory holds the user's only copy of in-progress skills, so
the capability is deliberately minimal and enforced by an allow-list rather than
by convention.

Distinct from `claude-acs backup` (versioned content archive, outbound) and
`claude-acs save` (account profiles). Neighbouring subsystems, different jobs.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | The setup: LEGEND of every term, why the caution is warranted, the six pull gates and their exit codes, scheduling traps, the run-log outcome table, failure behaviour. |
| [`config.json`](config.json) | All tunables: the one permitted target repo and its expected remote, staleness threshold, log path, daily schedule. Carries the pull-only policy statement. |
| [`scripts/`](scripts/INDEX.md) | `gitops` (the git allow-list chokepoint), `runlog`, `schedule` behind the `skills_sync.py` CLI, plus the test suite. |

Design context: [`../../docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md`](../../docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md).

Related: [`../claude-skills-backup/`](../claude-skills-backup/INDEX.md), whose
conventions this setup mirrors — LEGEND-led README, `config.json` as the single
source of truth for cadence, stdlib-only `scripts/`, launchd plist generated
from config, `claude-acs <verb>` wrapper.

**Scope note:** the consolidation design retires `bootstrap-sync` because a
self-contained `acs-claude-skills` makes provisioning a plain `git clone`. That
solves *provisioning*; this setup solves *staying current*, which a clone does
not. It takes no position on skill content and never pushes.
