# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-09-21

- **The usage-stats dashboard now has a written-down theme**
  ([`setups/claude-code-usage-stats/THEME.md`](setups/claude-code-usage-stats/THEME.md)).
  The palette already existed in `dashboard/template.html`; this records it with
  measured contrast for every token — eight categorical series (worst case
  `--s2` at 4.37:1, against a 3:1 mark floor), a five-step sequential heat
  scale, a reserved warn triad — plus the denylist. No colour values changed.
- Enforced by a second brand profile,
  [`usage-dashboard.json`](setups/impeccable-brand-lint/brands/usage-dashboard.json),
  hash-linked to `THEME.md`. It needs **no** `ignoreRules`: unlike Casenote,
  which mandates Inter and must suppress `overused-font`, the dashboard uses the
  system stack and upstream objects to nothing. The suppression list is a
  property of the brand, not of the tool.
- Two linter bugs found by pointing it at a real page rather than its fixtures:
  - `status-as-series` flagged `color: var(--warn-ink)` on `--warn-bg` — the
    warn triad doing its job. Narrowed to the mark surface (`fill`/`stroke`),
    where categorical series actually live; `color` and `border-*` are UI chrome.
  - A profile's relative `source_file` resolved against the process cwd, so the
    staleness check passed from the repo root and raised a false alarm from
    `scripts/`. Now anchored to the repo root.
- Stats data refreshed: 10,150 prompts across 4 accounts, 280 projects, 1,296
  sessions, 2026-02-16 → 2026-09-19. Output stays in `~/.cache/claude-acs-stats`
  and is never committed, per the setup's privacy rule.
- 61 tests in `impeccable-brand-lint`.

## 2026-09-20 (later)

- **`setups/impeccable-casenote` renamed to
  [`setups/impeccable-brand-lint`](setups/impeccable-brand-lint/README.md) and
  made brand-agnostic.** v1 hard-coded one brand's hexes, token names and
  `ignoreRules` into the rule functions — wrong shape, since Impeccable itself
  is brand-neutral and `brand-guidelines` is already multi-brand.
- Five rules were already universal; the other four needed *values*, not
  different logic. They now read from a `brands/<brand>.json` profile, and
  return no findings when unconfigured rather than falling back to a default
  brand. `casenote_lint.py` became `brand_lint.py`; rule ids lost their
  Casenote vocabulary (`site-token-names` → `forbidden-token-names`,
  `accent-bright-as-mark` → `gradient-only-as-mark`, `seventh-series-colour` →
  `series-ceiling`).
- The same profile now emits the detector's `ignoreRules` via
  `--emit-impeccable-config`, so one brand drives both tools. In v1 that config
  was a separate hand-maintained file that could silently disagree.
- Profiles are hand-authored and record the `source_sha256` of their brand
  markdown; the linter exits 1 on drift, naming both hashes. Deriving profiles
  by parsing the brand files was rejected — their section shapes differ, so a
  parser tuned to one returns an empty profile for the others, and empty reads
  as "clean".
- A synthetic second brand (`Acme`) in the suite asserts its own violations are
  caught *and* that Casenote's tokens are invisible under it. Without that,
  every test would still pass on a Casenote-shaped engine. 51 tests.

## 2026-09-20

- [`setups/impeccable-casenote`](setups/impeccable-brand-lint/README.md) built and
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

- Design for [`setups/impeccable-casenote`](setups/impeccable-brand-lint/design.md)
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
