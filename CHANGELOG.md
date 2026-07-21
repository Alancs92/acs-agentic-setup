# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-07-19

- Deep-dived the token-cost tool cluster further: corrected an earlier
  wrong "headroom doesn't exist" finding (it moved orgs), and verified
  `caveman` and `andrej-karpathy-skills` against independent sources.
  Real-world combined impact of the whole tool stack: ~3.7% of spend in an
  independently reproduced replay, far below the marketed 60–90%.
- Adopted [`setups/token-cost-habits`](setups/token-cost-habits/README.md):
  `ccusage` for measurement (cadence: after expensive sessions + weekly,
  watch the cache_create/output split, not the total) and `caveman` as a
  situational output-compression skill.
- Started [`research/usage-patterns.md`](research/usage-patterns.md), a
  living note tracking actual Claude Code usage patterns — first use: decide
  whether `headroom` fits a docs-heavy workflow based on real `ccusage`
  data rather than a guess.

## 2026-07-18

- Initial scaffolding: repo structure (`agents/`, `setups/`, `research/`,
  `scripts/`, `templates/`), navigation conventions, and templates.
- First documented setup: [`setups/claude-code-web-github`](setups/claude-code-web-github/README.md)
  — Claude Code on the web running in a remote execution environment with
  the GitHub MCP integration, branch-per-task workflow, and PR auto-watch.
- `main` initialized as a minimal placeholder branch so this scaffolding
  could land through a reviewable pull request.
