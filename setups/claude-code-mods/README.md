# Claude Code mods

- **Created:** 2026-11-18
- **Status:** active
- **Agents involved:** Claude Code
- **Environment:** local
- **Topology:** single agent, multiple accounts

## What this is

Mods are small TypeScript hooks modules that ship inside Claude Code plugins.
They hook Claude Code's events and draw their own UI in the terminal (a band
above the prompt, a pane, a status line entry). A mod is a plugin, so it is
installed, enabled and updated like any plugin.

This setup holds the mods I run on every account:

| Mod | From | What it does |
|---|---|---|
| `token-weather` | `acs-mods` (mine, this folder) | One line above the prompt: context fill with a trend sparkline, tokens added per turn and turns left; the 5-hour and weekly usage limits with where each lands at its reset (red when the limit is hit first). |
| `next-steps-plus` | `acs-mods` (mine, adapted from the community `next-steps`) | Suggests up to three next prompts when a turn ends, informed by the repo's git state, the branch's PR and checks, and my notes; hover or focus one to see its full prompt; 1, 2 or 3 drafts it, 0 dismisses. It never puts ghost text in the prompt box. |

## Why

The status line shows a snapshot. `token-weather` shows the movement: how fast
the context grows per turn and whether the current pace runs out a usage
window before it resets. `next-steps-plus` makes the suggestions specific to
where the work stands (a failing check, unpushed commits, the next open item in
my notes) instead of to the conversation alone. Keeping the mods in a local marketplace lets every
account install the same copy, and keeping them in the profile manifest stops
`claude-acs apply-profile` from dropping them.

## Components

- **`~/.claude/mods/`**: the live local marketplace (`acs-mods`). Claude Code
  installs from it.
- **`mods/`** (this folder): a tracked copy of `~/.claude/mods/`, so the mods
  have history. It is a copy, not the live folder: after editing the live one,
  refresh it (below).
- **`../claude-code-account-profiles/profiles.json`**: lists both mods and both
  marketplaces in `core`, so every managed account keeps them. The `acs-mods`
  path is stored as `~/.claude/mods`; `apply_profile.py` expands `~` when it
  writes an account's `settings.json`.

### How `token-weather` reads its figures

It hooks `session.measure`, which Claude Code raises after each main-thread turn
and whenever a rate-limit window moves a whole point. That event carries the
context fill and `rateLimits` (`five_hour`, `seven_day`, each with `percentUsed`
and `resetsAt`). No log files are read.

- **5-hour projection:** the burn rate over the last hour of readings (needs 5
  minutes of readings), else the window's average pace.
- **Weekly projection:** the window's average pace only. A session-local rate
  would extrapolate one busy hour across days.
- **Weather glyph:** ☀ ☁ ☂ ⛈ from the worst of context fill, limit use and
  projection.
- **A drop before the reset is ignored.** Usage only falls when its window resets,
  so a lower reading before the reset time (the figures can flash 0%) keeps the
  last good one. Otherwise the next real reading would look like a huge burn and
  raise a false storm.

The forecasting is pure functions in `hooks/forecast.ts`; the band is in
`hooks/register.tsx`; tests are in `tests/`.

## Reproducing it

```bash
# 1. Put the marketplace in place (from this folder)
rsync -a mods/ ~/.claude/mods/

# 2. Per account: register the marketplaces and install the plugins
for a in harrison personal personal2 hai-enterprise; do
  export CLAUDE_CONFIG_DIR=~/.claude-accounts/$a
  claude plugin marketplace add ~/.claude/mods
  claude plugin install token-weather@acs-mods
  claude plugin install next-steps-plus@acs-mods
  # Optional: notes for the suggester. Paths are local, so they stay out of this repo.
  echo '{"notesFiles":"~/notes/{repo}/{branch_dashed}.md","notesIndexDirs":"~/notes/{repo}/handbook"}' |
    claude plugin configure next-steps-plus@acs-mods --values-stdin
done
```

`next-steps-plus` options and what it reads are in
[`mods/next-steps-plus/README.md`](mods/next-steps-plus/README.md).

For the managed accounts (personal, harrison, personal2), `claude-acs
apply-profile <account>` also enables both from the manifest. Restart running
sessions to load them.

## Changing a mod

```bash
# Edit ~/.claude/mods/token-weather/, then check and test it
claude plugin validate ~/.claude/mods/token-weather
claude plugin test ~/.claude/mods/token-weather

# Raise "version" in its .claude-plugin/plugin.json, then update the install
claude plugin marketplace update acs-mods
claude plugin update token-weather@acs-mods

# Refresh the tracked copy
rsync -a --delete ~/.claude/mods/ setups/claude-code-mods/mods/
```

The accounts share one plugins folder through a symlink, so one update reaches
all of them. Enabling stays per account.

## Notes / known issues

- **Enabling is per account.** The plugin files are shared across accounts, but
  each account's `settings.json` holds its own `enabledPlugins` and
  `extraKnownMarketplaces`. Installing in one account does not turn a plugin on
  in another.
- **`claude-community` is not registered by default** (only needed for the
  original `next-steps`, which `next-steps-plus` replaces). `claude plugin
  install next-steps@claude-community` fails with "not found in marketplace";
  add it first with `claude plugin marketplace add anthropics/claude-plugins-community`.
- **Two suggestion bands fight over 1/2/3.** Two plugins on one hotkey press
  neither, so keep only one of `next-steps` and `next-steps-plus` enabled.
- **Limit figures arrive with replies.** The 5-hour and weekly percentages are
  account-wide, but a session only gets a new reading when one of its own
  replies arrives. Usage in another session shows after this session's next
  turn. Off a subscription, `rateLimits` is empty and the band shows context
  only.
- **`~/.claude/mods/` is not versioned in place.** `~/.claude/.gitignore`
  ignores every top-level folder not allow-listed; the tracked copy here is the
  history.
- **`hai-enterprise` is not in the profile manifest.** Its two plugins were
  installed by hand with the commands above.
- **A mod being written loads from a session folder.** While Claude builds a mod
  it lives in `<account>/dev-mods/<session-id>/` and loads only in that
  session. Move it into `~/.claude/mods/` and install it to use it elsewhere.
