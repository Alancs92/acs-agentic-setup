# herdr

- **Created:** 2026-10-13 (trial) · adopted 2026-10-28
- **Status:** active
- **Agents involved:** Claude Code, herdr, cmux
- **Environment:** local
- **Topology:** multi-agent (one herdr session per cmux workspace, several Claude panes each)

## What this is

[herdr](https://herdr.dev) is the persistent runtime for long-running Claude sessions, running inside
cmux. Each cmux workspace hosts one named herdr session (`herdr --session <name>`), and each session
runs its own server with several Claude panes. This directory versions the config that makes the two
work together:

| File | Installs to | Role |
|---|---|---|
| `config/herdr/config.toml` | `~/.config/herdr/config.toml` | Status symbols, the `$mark` sidebar token, and the attention keybindings |
| `config/herdr/plugins/attention/` | `~/.config/herdr/plugins/attention/` | The `acs.attention` plugin: sticky manual marks and attention navigation |
| `config/claude-hooks/herdr-cmux-notify.sh` | `~/.claude-accounts/<acct>/hooks/` | Claude Notification/Stop hook that posts cmux notifications with herdr context |
| `config/zsh/herdr-cmux.zsh` | managed block in `~/.zshrc` | Hides cmux's surface ids inside herdr panes, and routes each herdr session to a Claude account |

## Why

- **Agents survive cmux.** The herdr server is daemonised, so quitting or crashing cmux only
  detaches. Reopening cmux re-runs `herdr --session <name>` and reattaches to the live agents.
- **The cmux restore conflict.** cmux's Claude wrapper switches on whenever `CMUX_SURFACE_ID` is set,
  and that variable leaked into herdr panes. cmux then saved herdr's Claude sessions against the
  hosting tab, keeping one slot per tab with the last write winning. On reopen it ran
  `claude --resume <last>` instead of herdr, so only one session came back. The zsh block moves the
  id to `HERDR_HOST_CMUX_SURFACE` and unsets the `CMUX_*_ID` variables, but only inside herdr panes.
- **Notifications that say where.** With cmux's hooks off inside herdr, `herdr-cmux-notify.sh`
  posts the cmux notification itself, against the hosting tab:
  `<cmux tab> · herdr-<session> › <space>`, with the pane's Claude title as subtitle. Clicking it
  goes to the right cmux tab. cmux skips the macOS banner while its window or that workspace is
  focused. That's by design.
- **herdr has no manual read state.** `done` clears the moment you view a pane, there is no API to
  re-arm it, and plugins can't add right-click entries. Upstream: issue #4622 was closed as not
  planned; the ideas are discussions #864, #691, #3855, #1722 and #906. The plugin adds a separate
  manual mark per pane, stored as a display-only pane metadata token, which nothing clears except
  you.
- **One session, one account.** herdr is used with both the `harrison` and `personal` accounts. A
  pane's shell used to start on the default account (`claude-acs default`), so a personal session
  needed a manual `claude-acs sw personal` in every pane. Routing by session name fixes that.

## Account routing

Each herdr pane starts a login zsh with `HERDR_SESSION` set, so the zsh block picks the
account at shell start and runs `claude-acs switch <account>` quietly:

| Session | Account |
|---|---|
| `personal` | `personal`: the name equals an account |
| `management-personal` | `personal`: the name ends in `-<account>` |
| `research-personal2` | `personal2`: the longest matching account wins |
| `annalise-v2`, `Frontier` | unchanged: the default account |

An account is a directory under `$CLAUDE_ACCOUNT_STORE` (`~/.claude-accounts`) with a
`settings.json`, so a new account routes as soon as it is saved, with no list to maintain. Matching
is case-sensitive. Routing only runs when the shell starts: a later `claude-acs sw <other>` in a pane
still works, and new panes go back to the routed account. The block has to come after
`source ~/claude_code_toggle.sh` in `~/.zshrc`, which it does when `install.sh` appends it. Without
`claude-acs` defined, routing does nothing.

## Components

- **herdr** (Homebrew, 0.9.x): sessions, panes, agent detection, the sidebar.
- **cmux**: the outer terminal and notification centre.
- **`acs.attention` plugin**, prefix `ctrl+b`:

  | Key | Action |
  |---|---|
  | `u` | Cycle the mark: ↩️ later → 👀 review → ⏳ waiting → none |
  | `shift+u` | Clear the mark |
  | `a` | Jump to the next agent needing attention: blocked, then done, then marked |
  | `shift+a` | Mark all done agents as seen, then return to the current pane |

  Marks are stored by Claude session id in `~/.local/state/herdr/plugins/acs.attention/marks.json`,
  so a herdr restart that resumes agents into new panes keeps them. A `[[startup]]` hook re-applies
  them, retrying for up to 90 s while agents report in. Each mark records when its pane was last
  seen. On the last retry, marks unseen for 14 days are dropped, so marks of finished Claude
  sessions don't pile up.

## Reproducing it

```bash
brew install herdr
setups/herdr/scripts/install.sh            # every saved account
setups/herdr/scripts/install.sh personal   # or only the accounts named
```

For each account (a directory under `~/.claude-accounts` with a `settings.json`), `install.sh`
installs herdr's Claude integration if it isn't current, copies the notify hook into `hooks/` and
wires it into `settings.json`. Any session can route to any account, so every account gets them.
Outside herdr the hook exits at once. An account without a `settings.json` is skipped with a
message. Start Claude with it once, then re-run.

It copies files (not symlinks, because this repo lives in worktrees that come and go), backs up any
differing live file as `*.bak-<timestamp>-<pid>`, writes the zsh block, links the plugin and reloads
every running session. The zsh block sits between `# >>> herdr-cmux >>>` and
`# <<< herdr-cmux <<<`. The first run appends it. Later runs replace it in place, including the old
unmarked block, so repo edits reach `~/.zshrc`. It is idempotent: a second run changes nothing. A
failed session reload is only a warning. The exit code is non-zero only when a `settings.json` or
`~/.zshrc` couldn't be updated.

After editing the live config in place, bring it back into the repo with `scripts/capture.sh
[account]`, then commit. The hook is the same file in every account, so with no argument it is
taken from the first account that has it.

Tests (fake `HOME`, fake `herdr`; nothing live is touched):

```bash
python3 setups/herdr/scripts/test_herdr_setup.py
```

## Config

No secrets. `config.toml` assumes the default `ctrl+b` prefix, and `prefix+n` is herdr's next-tab
key, which is why the jump binding is `a`.

## Notes / known issues

- Claude panes started **before** the zsh block existed, or changed, keep their old environment
  (cmux ids, account) until their shell restarts.
- The notify hook is wired per account. An account without it gets no notifications for Claude
  inside herdr, because cmux's own hooks are off there. Re-run `install.sh` after saving a new
  account.
- Pane-keyed marks (panes with no Claude session id) can land on the wrong pane if herdr reuses
  the pane id after a restart.
- Marking an agent means focusing it first, which consumes its `done` badge. herdr has no agent
  context for plugin actions.
- Not yet exercised: `seen-all` and `next` across many sessions, mark restore after a reboot.
