# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-08-25

- Consolidated skill ownership. `~/.claude/skills` tracked 13 entries as git
  symlink objects whose content was an **absolute path containing the
  username**, so cloning the skills repo on any other machine produced dangling
  links instead of skills. Those 13 are now real files, the stale
  `claude-bootstrap/skills/` (19, untouched since February, carrying a drifted
  duplicate `git-worktrees`) is retired, and the dependency direction is
  reversed: the bootstrap repo is a curated export target rather than an input.
  Provisioning a machine is now a `git clone`.
  See [`docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md`](docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md).
- New setup: [`setups/claude-skills-sync`](setups/claude-skills-sync/README.md)
  — `claude-acs skills {pull,status,install-schedule}`, a daily pull-only
  launchd job. Deliberately one-directional: automating the write side of a
  working tree you edit by hand means auto-committing half-finished work.
  Safety is structural — an allow-list of 8 git subcommands with `merge` pinned
  to `--ff-only`, so `reset`/`clean`/`stash`/`checkout`/`rebase`/`push` are
  unreachable rather than merely unused.

## 2026-08-24

- New setup: [`setups/claude-skills-backup`](setups/claude-skills-backup/README.md)
  — `claude-acs backup`, a versioned, hash-deduped backup of hand-authored
  Claude config (skills, commands, agents, hooks, per-account settings, plugin
  manifests). Snapshots only what changed; timestamps are excluded from the
  content hash so a `touch` or a cloud resync costs nothing. Retention does what
  git structurally cannot — keep 30 days, thin weekly past 30 and monthly past
  90, collapse a unit untouched for 180 days to its newest two, with a hard
  floor of one surviving snapshot. Blobs live in OneDrive rather than git,
  because anything committed can never be pruned; the audit trail is a
  byte-stable `catalog.jsonl` that diffs as plain English.
  See [`docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md`](docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md).

## 2026-08-23

- New setup: [`setups/claude-code-usage-stats`](setups/claude-code-usage-stats/README.md)
  — `claude-acs stats`, cross-account usage statistics emitting a versioned
  `stats.json` plus a standalone 13-panel HTML dashboard over prompt history
  and session transcripts, with `(timestamp, display)` dedup across accounts.

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
