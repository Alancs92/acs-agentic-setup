# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-07-25

- Token-usage audit ([`research/token-usage-audit.md`](research/token-usage-audit.md))
  + reusable baseline methodology
  ([`research/baseline-metric-methodology.md`](research/baseline-metric-methodology.md)):
  measured a lean 18-plugin Claude Code profile cutting the startup context tax
  ~20% (via built-in tool schemas; MCP is deferred to 0 tokens).
- New setup: [`setups/claude-code-account-profiles`](setups/claude-code-account-profiles/README.md)
  — a shared lean-core plugin/skill profile applied per account via a new
  `claude-acs apply-profile <account>` subcommand, with per-account overlays,
  reversible skill pruning (opt-in; the skills dir can be shared across
  accounts), and a golden-hash regression anchor.

## 2026-07-18

- Initial scaffolding: repo structure (`agents/`, `setups/`, `research/`,
  `scripts/`, `templates/`), navigation conventions, and templates.
- First documented setup: [`setups/claude-code-web-github`](setups/claude-code-web-github/README.md)
  — Claude Code on the web running in a remote execution environment with
  the GitHub MCP integration, branch-per-task workflow, and PR auto-watch.
- `main` initialized as a minimal placeholder branch so this scaffolding
  could land through a reviewable pull request.
