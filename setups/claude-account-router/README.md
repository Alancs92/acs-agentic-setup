# claude-account-router

- **Created:** 2026-11-13
- **Status:** active (built and tested; live install pending)
- **Agents involved:** Claude Code, Orca, herdr, cmux
- **Environment:** local
- **Topology:** single wrapper in front of every `claude` launch

## What this is

A `claude` wrapper that picks the `claude-acs` account for each launch from rules, then runs
the real `claude`. It is installed ahead of the real one on PATH, so anything that starts
`claude` by name goes through it: a terminal, a herdr pane, an Orca tab, Orca's chat view, an
Orca restore, an agent spawning another agent. The account is applied the way
`claude-acs switch` applies it (config dir plus that account's telemetry and privacy env), by
sourcing `~/claude_code_toggle.sh` and calling it.

| File | Installs to | Role |
|---|---|---|
| `bin/claude` | `~/.local/share/claude-router/bin/` | The wrapper |
| `bin/claude-route` | same | `which`, `check`, `pin`, `run`, `log` |
| `lib/claude-router.zsh` | `~/.local/share/claude-router/lib/` | The decision logic, shared by both |
| `config/rules.example` | `~/.config/claude-router/rules` (first install only) | Rule syntax and examples |
| `config/zsh/claude-router.zsh` | managed block in `~/.zshrc` | Puts the router first on PATH; marks a manual `claude-acs sw` as a choice |

## Why

- **One account per launch, decided by where and how it starts.** herdr routes by session
  name, but Orca, plain terminals and restored panes start on whatever the shell's default
  is. Orca's own account switcher is global, ignores `claude-acs` and drops its per-account
  telemetry env. Its per-project accounts (PR stablyai/orca#23228) are unmerged and use
  Orca's own logins, not `~/.claude-accounts`.
- **Not tied to Orca.** The rules live with us and work for every way of starting `claude`.
  Orca stays on "System default" (no managed accounts) and is only the UI.
- Research and the Orca findings: textx-docs
  `Projects/acs-agentic-setup/research/orca-vs-cmux-herdr.md`.

## How a launch is decided

First hit wins. `claude-route which [dir]` prints the result and the reason.

1. **`CLAUDE_ROUTE_ACCOUNT`**: a one-off (`claude-route run personal ...`) or an automation.
2. **An account someone already chose**: `CLAUDE_CONFIG_DIR` set by a manual `claude-acs sw`,
   a herdr session routed by name, or a script. What does *not* count as a choice: the
   default account an interactive shell starts on (the zsh block exports
   `CLAUDE_ROUTER_SHELL=1`, and `claude-acs sw` clears it), and a config dir another tool
   picked (`CLAUDE_ROUTER_MANAGED_DIRS`, default `*/orca/claude-profiles/*`).
3. **A repo pin**: `git config claude.account`, set with `claude-route pin <account>`. In a
   bare repo it is shared by every worktree.
4. **The rules file**, first matching line.
5. **Nothing matched**: the shell's account stays, or the default if there is none.

If the chosen account isn't saved, the toggle is missing, or the switch doesn't take, the
wrapper refuses to start `claude` rather than run it on another account. Once the wrapper has
switched, it clears `CLAUDE_ROUTER_SHELL`, so a `claude` started from inside that session
keeps the same account.

## Rules

`~/.config/claude-router/rules`, one rule per line: `<account> [condition ...]`. All conditions
must hold; a line with no conditions always matches.

| Condition | Matches | Case |
|---|---|---|
| `path=<glob>` | the launch folder or anything inside it (`~` allowed, symlinks resolved) | sensitive |
| `remote=<glob>` | `git remote get-url origin` | insensitive |
| `repo=<glob>` | repo name from origin, else the folder holding `.git`/`.bare` | insensitive |
| `branch=<glob>` | current branch | sensitive |
| `host=<glob>` | short hostname | insensitive |
| `env:NAME=<glob>` | an environment variable; `env:NAME=?*` means set and non-empty | sensitive |

Prefix `!` to negate. Globs are zsh extended globs, and `*` also matches `/`.

```text
personal  remote=*[:/]Alancs92/*                 # personal GitHub repos
personal  path=~/repos/side-projects
personal  env:ORCA_PANE_KEY=?* repo=textx-v2     # only in Orca, only this repo
harrison  branch=V2B-*
```

## Using it with Orca

Tested end to end against Orca 1.4.258 (`scripts/e2e_orca.sh`):

- Leave Orca on **System default**: add no Claude accounts in Orca. Its `claude` shell
  function then always hands off to `command claude`, which is the router.
- Every Orca entry point went through the router and got the rule's account: a `claude`
  typed in an Orca terminal (what a restored pane runs: `claude --resume <id>` in the
  worktree), `orca agent launch --surface terminal`, and the native chat view
  (`--surface chat`), which Orca spawns without a shell.
- Orca exports `ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKSPACE_ID` and `ORCA_WORKTREE_ID` into
  its terminals, so `env:` rules can single out Orca (`env:ORCA_PANE_KEY=?*`) or one Orca
  workspace.
- To set a repo's account from inside Orca, run `claude-route pin <account>` in any of its
  terminals.
- Orca's usage readout runs `claude` from its own data folder, so it reports the default
  account unless a rule says otherwise.

## Reproducing it

```bash
setups/claude-account-router/scripts/install.sh
$EDITOR ~/.config/claude-router/rules     # or: claude-route edit
claude-route check
claude-route which ~/repos/<some-repo>
```

`install.sh` copies the files (not symlinks: the repo lives in worktrees), backs up any
differing live file as `*.bak-<timestamp>-<pid>`, creates the rules file only if it is
missing, and writes the managed block at the end of `~/.zshrc`, after
`source ~/claude_code_toggle.sh` and the herdr block. Re-runs replace the block in place.
Open shells pick it up when they restart. To turn the router off for one launch:
`CLAUDE_ROUTER_DISABLE=1 claude`; for good: delete the block.

Tests (fake `HOME`, fake accounts, fake `claude`; nothing live is touched):

```bash
python3 setups/claude-account-router/scripts/test_claude_router.py   # 35 unit and shell tests
setups/claude-account-router/scripts/e2e_orca.sh                     # Orca end to end, if installed
```

`e2e_orca.sh` runs a headless `orca serve` on 127.0.0.1 with pairing off, its own data dir,
fake accounts and a fake `claude`. Orca starts terminal shells through `/usr/bin/login`,
which resets `HOME` to the real one, so the script reaches those shells through `ZDOTDIR`
and a toggle copy whose account store is inside the sandbox. A sandbox that only changes
`HOME` is not enough: the shells then load the real `~/.zshrc` and accounts. Re-run it after
each Orca update.

## Config

No secrets. Environment overrides: `CLAUDE_ROUTER_RULES`, `CLAUDE_ROUTER_TOGGLE`,
`CLAUDE_ROUTER_LOG`, `CLAUDE_ROUTER_MANAGED_DIRS`, `CLAUDE_ROUTER_REAL_CLAUDE`,
`CLAUDE_ROUTER_VERBOSE=1` (print the decision to stderr), `CLAUDE_ROUTER_DISABLE=1`.

Each launch appends one line to `~/.local/state/claude-router/decisions.log`: time, account,
action, folder, Orca/herdr context and reason. Never the arguments, which can hold prompts.
`claude-route log` shows the tail.

## Notes / known issues

- Only launches that resolve `claude` by name are routed. Scripts that run
  `~/.local/bin/claude` directly (the morning-briefing and EOD launchers) set
  `CLAUDE_CONFIG_DIR` themselves and are unaffected.
- A script that exports the default account's `CLAUDE_CONFIG_DIR` from inside an interactive
  shell still carries `CLAUDE_ROUTER_SHELL=1`, so rules can override it. Use
  `CLAUDE_ROUTE_ACCOUNT` there.
- Adds about 0.07 s to a launch that keeps its account and about 0.1 s to one that switches.
- Not covered by the e2e script: an Orca **desktop** cold restore. Headless `orca serve` does not
  restore panes; the restore path is the `claude --resume <id>` launch that is covered.
