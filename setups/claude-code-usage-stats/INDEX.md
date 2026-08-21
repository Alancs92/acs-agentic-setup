# setups/claude-code-usage-stats/

Cross-account Claude Code usage statistics. One command —
`claude-acs stats` — walks every account on the machine, emits a versioned
`stats.json` and renders a standalone 13-panel `dashboard.html`. Source of truth
is `scripts/aggregate.py` (it owns the schema); generated output lives outside
the repo in `~/.cache/claude-acs-stats/`, because the data is personal.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | The setup: what it is, why, flags, the three-figure dedup model, privacy, measured performance. |
| [`design.md`](design.md) | Approved design: data model, `stats.json` schema, correctness traps, UTC decision, YAGNI cuts. |
| [`implementation-plan.md`](implementation-plan.md) | The eight-task TDD build record, one module per task. |
| [`scripts/`](scripts) | The collector: `discovery`/`history`/`transcripts`/`cache`/`aggregate`/`render` behind the `collect_stats.py` CLI, plus 49 tests. |
| [`dashboard/`](dashboard) | `template.html` — vanilla JS + inline SVG, no chart library, no CDN, no build step. |

Related: [`../claude-code-account-profiles/`](../claude-code-account-profiles/INDEX.md),
whose `claude-acs` wrapper split (repo-as-truth, shell fn as ergonomic entry)
this setup mirrors.
