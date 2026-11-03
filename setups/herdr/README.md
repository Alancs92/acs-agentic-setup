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
| `config/zsh/herdr-cmux.zsh` | appended to `~/.zshrc` | Hides cmux's surface ids inside herdr panes |

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
  them, retrying for up to 90 s while agents report in.

## Reproducing it

```bash
brew install herdr
CLAUDE_CONFIG_DIR=~/.claude-accounts/harrison herdr integration install claude
setups/herdr/scripts/install.sh harrison personal   # accounts that run Claude inside herdr
```

`install.sh` copies files (not symlinks, because this repo lives in worktrees that come and go),
backs up any differing live file as `*.bak-<timestamp>`, wires the hook into each account's
`settings.json`, appends the zsh block once, links the plugin and reloads every running session. It
is idempotent: a second run changes nothing.

After editing the live config in place, bring it back into the repo with `scripts/capture.sh`, then
commit.

## Config

No secrets. `config.toml` assumes the default `ctrl+b` prefix, and `prefix+n` is herdr's next-tab
key, which is why the jump binding is `a`.

## Notes / known issues

- Claude panes started **before** the zsh block existed still carry the cmux ids until restarted.
- The notify hook is wired per account. An account without it gets no notifications for Claude
  inside herdr, because cmux's own hooks are off there.
- Marking an agent means focusing it first, which consumes its `done` badge. herdr has no agent
  context for plugin actions.
- Not yet exercised: `seen-all` and `next` across many sessions, mark restore after a reboot.
