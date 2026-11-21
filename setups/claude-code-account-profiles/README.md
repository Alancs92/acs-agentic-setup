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

## The shell entry point

`claude-acs regroup` (`rg`) starts the `/regroup` ritual: a fresh Sonnet session
launched from `$HOME` with `CLAUDE_CONFIG_DIR` set (subshell-scoped) to
`$CLAUDE_ACCOUNT_STORE/${CLAUDE_ACS_REGROUP_ACCOUNT:-harrison}`. It pre-mints a
`--session-id` and appends it to `~/.claude/regroup/skip-sessions` so tide
attribution can skip regroup sessions. Test overrides: `CLAUDE_ACS_REGROUP_DIR`,
`CLAUDE_ACS_CLAUDE_BIN`.

`claude-acs discord` (`dc`) starts Claude on the personal account with the
Discord channel plugin. It reads the bot token from 1Password into the plugin's
`.env` (mode 600) for the session and deletes it on exit; the token is never
exported. A stale `.env` is removed before writing, since `umask` only applies to
new files. The file is also removed if the terminal closes or the shell is sent
SIGTERM mid-session, and Ctrl-C inside Claude doesn't skip the clean-up. The 1Password account and item are not in this repo: the function
sources them from `$CLAUDE_ACCOUNT_STORE/.discord-op`, a private file that sets
`CLAUDE_ACS_DISCORD_OP_ACCOUNT` and `CLAUDE_ACS_DISCORD_OP_REF`. Test overrides:
`CLAUDE_ACS_DISCORD_ACCOUNT`, `CLAUDE_ACS_DISCORD_DIR`, `CLAUDE_ACS_CLAUDE_BIN`.

`claude-acs` itself is a zsh function, not a script. It lives at
`~/claude_code_toggle.sh`, sourced from `.zshrc`, and **stays there** — it is used
constantly and relocating it buys nothing.

`shell/claude_code_toggle.sh` is a tracked copy, so the file has history and a
recoverable version. It is a copy, not the live file: after editing the live one,
refresh it.

```bash
cp ~/claude_code_toggle.sh setups/claude-code-account-profiles/shell/claude_code_toggle.sh
```

`scripts/test_toggle_shell.py` guards the three ways it has broken: a dispatch
target that no longer exists, helpers named with a single underscore (Claude Code
strips those from shell snapshots, so every subcommand fails inside Claude Code and
nowhere else), and this copy drifting from the live file. It also covers which
accounts get telemetry by default, the `discord` launch (token handling, exit code,
clean-up, failures), and that no 1Password id lands in this public copy. The drift test skips
where no live file exists, so it stays honest on other machines.

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

## Mirror accounts

A **mirror** is an existing account under a different Claude login, kept in
parity with it instead of tuned on its own. Declared in `profiles.json`:

```json
"mirrors": { "terradev": "personal" }
```

`claude-acs mirror [account] [--check|--dry-run]` (`scripts/mirror_account.py`)
syncs it. With no account it syncs every declared mirror.

| Part | How | Parity |
|---|---|---|
| `CLAUDE.md`, `hooks/`, `agents/`, `memory/`, each `projects/<slug>/memory/` | symlink → source | live |
| `skills/`, `commands/`, `plugins/` | symlink → canonical `~/.claude` | live |
| `settings.json`, `settings.local.json`, `.mcp.json` | copied, `<store>/<source>/` paths rewritten | on each `mirror` run (source wins, old file backed up) |
| `.claude.json` | only `mcpServers` + per-project `hasTrustDialogAccepted` merged in | on each `mirror` run |
| login, history, sessions, transcripts, caches | untouched | never shared |

Settings are copied, not linked, because Claude Code rewrites them itself and an
atomic rename would silently replace a symlink. So **change settings on the
source**, then run `claude-acs mirror`. A setting changed on the mirror is reset
by the next run (`--check` shows which keys drifted first).

Auto-memory a mirror creates for a new project is moved into the source and
linked back on the next run. A project with real memory dirs on both sides is
reported as a conflict and left alone.

`apply-profile` refuses a mirror and points at its source: apply the profile to
`personal`, then `claude-acs mirror`.

**Onboarding a new mirror:** add it to `mirrors`, run `claude-acs mirror <name>`,
then `claude-acs sw <name>` → `claude` → `/login`. The account dir needs no
manual setup. Telemetry follows `CLAUDE_OTEL_ORG_ACCOUNTS` in the toggle
(not listed → off).

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
