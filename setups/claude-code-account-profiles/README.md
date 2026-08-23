# Per-account Claude Code profiles

- **Created:** 2026-07-25
- **Status:** active
- **Agents involved:** Claude Code
- **Environment:** local
- **Topology:** single agent, multiple accounts

## What this is

A shared **lean-core** plugin/skill definition that any Claude Code account can
apply, with small per-account tweaks. One manifest (`profiles.json`) is the
source of truth; `apply_profile.py` resolves + applies it to a single account's
`settings.json`; `claude-acs apply-profile <account>` is the ergonomic entry.

Born out of the token-usage audit
([`../../research/token-usage-audit.md`](../../research/token-usage-audit.md)),
which found a lean 18-plugin profile cut the startup context tax ~20% — and that
the win comes via built-in tool schemas, not MCP (which is deferred to 0). This
setup makes that lean profile reproducible across the `personal`, `harrison`,
and `personal2` accounts. Full rationale + design: [`design.md`](design.md).

## Why

Three accounts, each with its own `settings.json`, drift apart when tuned by
hand. This keeps a single definition of "lean" while letting each account differ
by explicit `add`/`remove` diffs — no copy-paste, no drift, reproducible.

## Components

- **`profiles.json`** — `core` (18 plugins + marketplaces + skill prune-list)
  and per-account overlays (`plugins_add` / `plugins_remove` / `skills_prune`).
- **`scripts/apply_profile.py`** — pure resolver + safe applier (stdlib only).
- **`claude-acs apply-profile`** — wrapper in `~/claude_code_toggle.sh`.

## Reproducing it

```bash
# Preview (safe; no writes). Plugins only by default.
claude-acs apply-profile personal --dry-run

# Apply the plugin profile to an account (backs up settings.json first).
claude-acs apply-profile personal --yes

# Also prune real-dir skills (opt-in — the skills dir may be SHARED across
# accounts; the tool warns before touching it).
claude-acs apply-profile personal --yes --prune-skills

# Direct invocation (no claude-acs):
python3 scripts/apply_profile.py --account personal --dry-run
```

Point the wrapper at a non-default checkout with
`export CLAUDE_ACS_PROFILES_REPO=/path/to/acs-agentic-setup`.

Restart Claude Code after applying — plugins reload on launch. Verify with
`/context` (see the audit's baseline protocol).

## Config

- Manifest: [`profiles.json`](profiles.json). `core.plugins` is pinned to the
  applied set; the test suite asserts `personal` resolves to config hash
  `d8e09889…` (base lean-18 `59a9cd93…` + `mattpocock-skills`, added 2026-07-27).
- No secrets. The tool only writes `enabledPlugins` + `extraKnownMarketplaces`
  and moves named skills; it never touches hooks/env/permissions/model.

## Notes / known issues

- **Skill pruning is opt-in** (`--prune-skills`) because an account's `skills/`
  can be a symlink to a shared dir (`personal/skills → ~/.claude/skills`);
  pruning then affects every account sharing it. The tool warns when it detects
  this. Pruned skills are **moved** to `<account>/skills/.profile-pruned-skills/`
  (restorable), never deleted.
- **Bootstrap-symlinked skills** (`code-review`, `skill-creator`) are
  reported+skipped in v1; honouring their prune needs a `bootstrap-sync` change (v2).
- **`harrison` / `personal2`** overlays are populated and **applied** (2026-07-27):
  harrison → 22 (`5fe17266…`), personal2 → 18 (`ed1f130f…`), personal → 19 (`d8e09889…`).
- **`mattpocock-skills` (bundle of ~21 skills) is in core** on all three accounts.
  It re-adds to the Skills row (pure Skills-row cost; MCP/System-tools unaffected).
  **Future iteration:** once we know which of its skills are actually useful/used a
  lot, `/context`-measure the bundle's real cost and decide whether to keep the
  full bundle or wait for individual-skill install
  ([mattpocock/skills#610](https://github.com/mattpocock/skills/issues/610)).
- Tests: `python3 scripts/test_apply_profile.py` (17 tests).
