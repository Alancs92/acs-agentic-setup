# Settings kept in sync across accounts

- **Created:** 2026-09-24
- **Status:** active
- **Scope:** every `claude-acs` account profile plus the `~/.claude` fallback

A list of Claude Code user settings that are meant to be identical on every
account, and how to change one without breaking the rest of each file.

## The rule

**A user setting only takes effect for an account if it is in *that account's*
`settings.json`.** A change meant for "all accounts" has to go into every file
below.

Why:

- `claude-acs sw <account>` sets `CLAUDE_CONFIG_DIR=~/.claude-accounts/<account>`.
  Claude Code reads user settings from `$CLAUDE_CONFIG_DIR/settings.json` only,
  so `~/.claude/settings.json` is **ignored** while an account is active. It
  applies only when `CLAUDE_CONFIG_DIR` is unset.
- Only `skills`, `commands` and `plugins` are shared, as symlinks to canonical
  `~/.claude` (see `~/.claude-accounts/README.md`). `settings.json` is a real
  per-account file.
- The files differ on purpose. For example, `harrison` has work-only hooks, and
  each account has its own allow list. Symlinking one `settings.json` into every
  account would erase that, so it is not done.
- The only true central option is the system-wide managed-settings file
  (`/Library/Application Support/ClaudeCode/managed-settings.json`). It needs
  `sudo`, applies to every account, and has the highest precedence. Rejected as
  too heavy for simple defaults.
- `apply_profile.py` (this setup) already writes per-account `settings.json`,
  but it manages only `enabledPlugins` and `extraKnownMarketplaces`. The
  settings listed below are **not** in `profiles.json` yet. A future
  `core.settings` block there would make them truly centralised.

## Files an "all accounts" change must touch

| File | Applies when |
|---|---|
| `~/.claude-accounts/harrison/settings.json` | `claude-acs sw harrison` (work) |
| `~/.claude-accounts/personal/settings.json` | `claude-acs sw personal` |
| `~/.claude-accounts/personal2/settings.json` | `claude-acs sw personal2` |
| `~/.claude/settings.json` | `CLAUDE_CONFIG_DIR` unset (fallback) |

Source of truth for the account list: `ls ~/.claude-accounts/` (a directory
with a `.claude.json` in it is an account). When a new account is added, apply
every row of the register to it.

`~/.claude-accounts` is a git repo, but it tracks only each account's
`CLAUDE.md`. Settings edits leave no git trail there, so this file is the record.

## Register

| Setting | Value | Since | Why |
|---|---|---|---|
| `permissions.defaultMode` | `"auto"` | 2026-09-24 (`harrison` already had it; added to `personal`, `personal2`, `~/.claude`) | Sessions start in auto mode: a classifier approves routine actions and still stops risky or destructive ones. `permissions.deny`/`ask` rules still apply on top. Switch per session with Shift+Tab. |

## Apply a synced setting

Merge with `jq` so existing keys and arrays (allow lists, hooks) survive:

```bash
for f in ~/.claude-accounts/{harrison,personal,personal2}/settings.json ~/.claude/settings.json; do
  cp -p "$f" "$f.bak-$(date +%Y%m%d)"                      # rollback copy
  jq '.permissions.defaultMode = "auto"' "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm "$f.tmp"
done
```

`cat > "$f"` rather than `mv` keeps the original file's permissions
(`~/.claude/settings.json` is `600`).

## Verify

```bash
for f in ~/.claude-accounts/*/settings.json ~/.claude/settings.json; do
  echo "$f: $(jq -r '.permissions.defaultMode // "unset"' "$f")"
done
```

Open sessions pick up the change on their next start. Invalid JSON silently
disables the **whole** file, so always check that `jq` parses it after a hand edit.
