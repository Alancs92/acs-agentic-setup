# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-09-20

- [`setups/impeccable-casenote`](setups/impeccable-casenote/README.md) built and
  active. Two composable checks: `fetch_engine.py` resolves a version-pinned
  Impeccable engine from its platform npm package, verifies the registry's
  dist.integrity sha512 and records provenance in a committed
  `engine.lock.json`; `casenote_lint.py` adds 9 rules encoding the
  mechanically-testable half of Casenote's denylist. Shared exit-code contract
  (0/1/2) so both run in one CI step. 35 tests.
- No fork of upstream was needed. `IMPECCABLE_BIN` points at a binary we hold,
  and the single rule Casenote disagrees with (`overused-font`, on Inter) is
  suppressed by one `ignoreRules` entry.
- The composition paid for itself immediately: upstream's contrast check caught
  two real defects in the hand-written clean fixture — a dark-mode block that
  forgot to re-declare `--surface` (1.7:1), and a bordered container with no
  inset — the first being a denylist item `casenote_lint.py` deliberately
  cannot encode.

## 2026-09-19

- Design for [`setups/impeccable-casenote`](setups/impeccable-casenote/design.md)
  — the scoped adoption the Impeccable evaluation recommended: upstream's
  detector, version-pinned and vendored, plus a `casenote-lint` checker encoding
  the mechanically-testable half of Casenote's denylist, both sharing one
  exit-code contract. `brands/alan-personal.md` remains the single source of
  truth for token values; nothing forks upstream. Designed only — not built.
- Fixed a `side-tab` anti-pattern in the usage-stats dashboard: `.utc-note`
  dropped its 3px accent `border-left`. Detector now clean (exit 0) on
  `dashboard/template.html`.

## 2026-09-18

- Evaluated [Impeccable](https://github.com/pbakaus/impeccable), a third-party
  design-language skill for coding agents
  ([`research/impeccable-design-skill.md`](research/impeccable-design-skill.md)).
  Ran its standalone detector against the usage-stats dashboard — one `side-tab`
  anti-pattern (`.utc-note`, 3px accent border + border-radius). Verdict: keep
  scoped — the zero-context-cost detector is the valuable half; the 24-command
  skill stays uninstalled until a personal project with sustained UI work can
  justify the slot against the lean profile.

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
