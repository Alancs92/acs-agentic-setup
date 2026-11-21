# Onboard a new Claude account

- **Created:** 2026-11-21
- **Status:** active
- **Covers:** plain Claude Code (`claude-acs`), herdr, Orca (through `claude-router`)
- **Worked example:** `terradev`, onboarded 2026-11-21 as a mirror of `personal`

Use this runbook to add any Claude login to this machine: personal, work, or a second login
of an existing setup. It goes in order. Do the steps for your account type and skip the rest.
Each setup's README has the detail. This page says which parts you need and in what order.

## 1. Decide two things first

**A. Mirror or standalone?**

| Type | Use when | What it shares |
|---|---|---|
| **Mirror** | Same setup as an existing account, only a different login (more usage, a second subscription). Example: `terradev` → `personal`. | CLAUDE.md, hooks, agents, memory, settings, MCP servers, all copied or linked from the source. You never configure it yourself. |
| **Standalone** | A different context with its own rules: a new employer or client, a separate org. Examples: `harrison`, `hai-enterprise`, `personal2`. | Only skills, commands and plugins (shared by every account). Everything else is its own. |

When unsure, pick mirror if the account would use the same CLAUDE.md and memory as an
existing one.

**B. Work or personal?** Org accounts get OpenTelemetry by default (`CLAUDE_OTEL_ORG_ACCOUNTS`
in the toggle). Every other account gets telemetry off plus the privacy opt-outs.

**Also pick:**

- **Account name**: letters, digits, `.`, `_`, `-`. It becomes the folder name, the herdr
  session suffix and the router's account name. Avoid a name that is the end of another
  account's name with a dash in front (see [Gotchas](#gotchas)).
- **Orca project suffix**: what an Orca project name ends in to route to this account. In use
  so far:

  | Account | Orca project suffix | herdr session | GitHub `remote=` rule |
  |---|---|---|---|
  | `personal` | `-personal` | `personal`, `*-personal` | `Alancs92`, `github-personal` |
  | `harrison` | `-hai` | `harrison`, `*-harrison` (or any other name: default account) | none (default account) |
  | `hai-enterprise` | `-enterprise` | `hai-enterprise`, `*-hai-enterprise` | none |
  | `terradev` | `-terradev` | `terradev`, `*-terradev` | none: no GitHub of its own; personal repos route to `personal` |

- **GitHub**: does the account have its own GitHub org or user? If not, add no `remote=` rule.
  Its repos keep following whichever account already owns that GitHub (terradev uses the
  personal GitHub, so `Alancs92` repos stay on `personal` unless an Orca project or herdr
  session picks terradev).

## 2. What each layer needs

| Layer | What makes the account work there | Where it lives | Mirror | Standalone |
|---|---|---|---|---|
| Account folder | `~/.claude-accounts/<name>/` | live only | `claude-acs mirror` creates it | you create it |
| Login | the account's own `/login` | macOS keychain, per folder | yes | yes |
| Shared skills, commands, plugins | symlinks to `~/.claude` | `setup-shared-resources.sh` | done by `mirror` | run it |
| Plugin profile | `profiles.json` | [`claude-code-account-profiles`](../../setups/claude-code-account-profiles/README.md) | `mirrors` entry | `accounts` entry, then `apply-profile` |
| Telemetry | `CLAUDE_OTEL_ORG_ACCOUNTS` | `shell/claude_code_toggle.sh` (tracked) = `~/claude_code_toggle.sh` (live) | only if work | only if work |
| herdr routing | a `settings.json` in the folder. **No list to edit.** | `~/.zshrc` herdr-cmux block ([`herdr`](../../setups/herdr/README.md)) | automatic | automatic once `settings.json` exists |
| herdr hooks (notifications, agent state) | `hooks/herdr-*.sh` wired into `settings.json` | `setups/herdr/scripts/install.sh` | inherited from the source | run `install.sh <name>` |
| Orca, plain terminals | a line in the router rules | `~/.config/claude-router/rules` ([`claude-account-router`](../../setups/claude-account-router/README.md)) | add a rule | add a rule |
| Login email prefill | `~/.claude-accounts/<name>.email` | live only | optional | optional |
| CLAUDE.md tracking | `.gitignore` of `~/.claude-accounts` | git repo in the account store | no (CLAUDE.md is a symlink) | yes |

Orca itself needs **no account setup**. Leave Orca's Claude accounts on **System default**
(add no accounts in Orca). Its `claude` then resolves to the router, which picks the account.

## 3a. Mirror account

Shown with `terradev` mirroring `personal`. Run in a worktree of this repo on the default
branch, or anywhere once the change is merged (`claude-acs` finds the scripts itself).

1. **Declare it** in `setups/claude-code-account-profiles/profiles.json`:

   ```json
   "mirrors": { "terradev": "personal" }
   ```

   Commit and merge (branch → PR). `claude-acs mirror` reads the copy on the default branch.

2. **Create and sync the folder**:

   ```bash
   claude-acs mirror terradev          # creates ~/.claude-accounts/terradev, links + copies
   claude-acs mirror --check           # exit 0 = in parity
   ```

3. **Log in**:

   ```bash
   claude-acs sw terradev && claude    # then /login with the new account
   # or: claude-route login terradev   (prefills from terradev.email if present)
   ```

4. **Telemetry**: only for a work mirror, see [step 4](#4-telemetry-work-accounts-only).
5. **herdr**: nothing to do. The mirror has `settings.json` and the source's herdr hooks.
6. **Orca and terminals**: [step 5](#5-orca-and-terminal-routing).
7. **Verify**: [step 6](#6-verify).

From then on, change settings on the **source** and run `claude-acs mirror`. A change made
on the mirror is reset by the next run.

## 3b. Standalone account

Shown with a new account `acme`. Copy the closest existing account as a start: `harrison`
for work, `personal` for personal.

1. **Create the folder with a settings file**:

   ```bash
   S=~/.claude-accounts; SRC=harrison; NEW=acme
   mkdir -p "$S/$NEW" && chmod 700 "$S/$NEW"
   sed "s#/.claude-accounts/$SRC/#/.claude-accounts/$NEW/#g" "$S/$SRC/settings.json" > "$S/$NEW/settings.json"
   cp -R "$S/$SRC/hooks" "$S/$NEW/hooks"          # the hook scripts settings.json points at
   cp "$S/$SRC/CLAUDE.md" "$S/$NEW/CLAUDE.md"     # then edit it for this account
   ```

   Rewriting the paths matters: copied hook commands would still point at the source's
   folder.

2. **Log in once** (this writes `.claude.json`, which marks the folder as an account):

   ```bash
   claude-acs sw acme && claude        # /login, then quit
   ```

3. **Share skills, commands and plugins**:

   ```bash
   ~/.claude-accounts/bin/setup-shared-resources.sh --dry-run
   ~/.claude-accounts/bin/setup-shared-resources.sh
   ```

4. **Plugin profile**: add `"acme": { "plugins_add": [], "plugins_remove": [], "skills_prune": [] }`
   under `accounts` in `profiles.json` (work accounts usually drop `obsidian@obsidian-skills`;
   personal ones drop `hai-agentic-dev@harrison-engineering`), merge it, then:

   ```bash
   claude-acs apply-profile acme --dry-run
   claude-acs apply-profile acme
   ```

5. **herdr hooks**:

   ```bash
   setups/herdr/scripts/install.sh acme
   ```

6. **Telemetry**: work accounts only, [step 4](#4-telemetry-work-accounts-only).
7. **Track its CLAUDE.md** in the account store's git repo: add to
   `~/.claude-accounts/.gitignore`

   ```text
   !/acme/
   acme/*
   !acme/CLAUDE.md
   ```

   then `git -C ~/.claude-accounts add .gitignore acme/CLAUDE.md` and commit.
8. **Settings meant for every account**: check
   [`synced-settings.md`](../../setups/claude-code-account-profiles/synced-settings.md) and
   add the new `settings.json` to its file list.
9. **Orca and terminals**: [step 5](#5-orca-and-terminal-routing). **Verify**: [step 6](#6-verify).

## 4. Telemetry (work accounts only)

Add the name to `CLAUDE_OTEL_ORG_ACCOUNTS` in the tracked toggle,
`setups/claude-code-account-profiles/shell/claude_code_toggle.sh`:

```sh
CLAUDE_OTEL_ORG_ACCOUNTS="${CLAUDE_OTEL_ORG_ACCOUNTS:-harrison hai-enterprise acme}"
```

Run `python3 setups/claude-code-account-profiles/scripts/test_toggle_shell.py`, copy the file
to `~/claude_code_toggle.sh`, and check `diff -q` shows the two match. Open shells pick it up
on restart. Personal accounts need nothing: off is the default.

## 5. Orca and terminal routing

herdr routes by session name on its own. Everything else (Orca terminals, Orca's chat view,
restored Orca panes, plain terminals) goes through the router's rules.

1. **Add rules** to `~/.config/claude-router/rules` (`claude-route edit`). First match wins,
   so keep the Orca project lines together at the top:

   ```text
   # Orca projects by name suffix (only inside Orca)
   personal        project=*-personal
   harrison        project=*-hai
   hai-enterprise  project=*-enterprise
   terradev        project=*-terradev
   acme            project=*-acme
   # Repos by remote, everywhere (optional)
   acme            remote=*[:/]acme-org/*
   ```

   `project=` only matches inside Orca. A `remote=` rule routes the repo in every app; add one
   only when the account has its own GitHub and all of that org's repos belong to it.

2. **Check them**:

   ```bash
   claude-route check                      # syntax
   claude-route which ~/repos/<some-repo>  # which account, and why
   ```

3. **In Orca**: name the project in Orca's sidebar so it ends in the suffix (`Website-acme`).
   For a single repo, run `claude-route pin acme` in one of its terminals instead (stored in
   `git config claude.account`, shared by every worktree of a bare repo).

The rules file is live-only (it holds org names). The repo carries only `rules.example`.

## 6. Verify

```bash
claude-route status                          # logged in, email, plan, expiry
claude-acs ls                                # the account is listed
claude-acs mirror --check                    # mirrors only: in parity
claude-route check                           # rules parse
```

herdr: start `herdr --session <name>` (or `<anything>-<name>`), open a pane, run
`echo $CLAUDE_CONFIG_DIR`. It should end in `/<name>`. In Claude, `/status` shows the login.

Orca: open a terminal in a project named `*-<suffix>`, run `claude-route which`. It should
name the account. With Orca closed, test a rule with a fake Orca CLI (this is how `terradev`
was checked):

```bash
S=$(mktemp -d); mkdir "$S/proj"
cat > "$S/fake-orca" <<EOF
#!/bin/sh
case "\$1 \$2" in
  "worktree list") echo '{"result":{"worktrees":[{"id":"r1::$S/proj","repoId":"r1","path":"$S/proj","displayName":"main"}]}}';;
  "project list")  echo '{"result":{"projects":[{"id":"p1","displayName":"Website-acme","sourceRepoIds":["r1"]}]}}';;
esac
EOF
chmod +x "$S/fake-orca"
env -u CLAUDE_CONFIG_DIR CLAUDE_ROUTER_SHELL=1 ORCA_PANE_KEY=x ORCA_WORKTREE_ID="r1::$S/proj" \
  CLAUDE_ROUTER_ORCA="$S/fake-orca" claude-route which "$S/proj"
# account: acme  (switch)
```

Last, `claude-route log` shows each real launch's account and reason.

## 7. Offboarding

1. Remove its router rules and any `claude-route pin` (`git config --unset claude.account`).
2. Remove it from `profiles.json` (`mirrors` or `accounts`) and from `CLAUDE_OTEL_ORG_ACCOUNTS`.
3. `claude-acs rm <name>` (switch away first). Shared skills, commands and plugins are
   symlinks, so they are not touched. For a mirror, its memory lives in the source and stays.
4. Remove `<name>.email`, its `.gitignore` lines and its row in `synced-settings.md`.

## Gotchas

- **herdr matches the end of the session name, longest account wins, case-sensitive.** An
  account called `prod` would also catch a session named `research-prod`. A session
  `management-personal` goes to `personal`, not `personal2`.
- **An account folder without `settings.json` is invisible to herdr routing** and is skipped
  by herdr's `install.sh`. Create `settings.json` before expecting herdr to route.
- **An account folder without `.claude.json` is skipped by `setup-shared-resources.sh`.** Log
  in once first (step 3b.2).
- **Don't use `claude-acs save` to start a new account.** It copies the current account's
  whole folder, including its `.claude.json` identity, projects and history. It exists to
  snapshot a login you just made in `~/.claude`.
- **A `project=` rule needs Orca running.** If Orca can't be asked when such a rule decides,
  the router refuses to start `claude` rather than guess (`CLAUDE_ROUTER_DISABLE=1 claude`
  bypasses once). A launch that matches an earlier non-Orca line never asks Orca. The live
  file puts the Orca lines first on purpose, so an Orca project name wins over a remote rule.
- **An explicit choice beats every rule**: `claude-acs sw`, a herdr session named for an
  account, `CLAUDE_ROUTE_ACCOUNT`, or a script setting `CLAUDE_CONFIG_DIR`. If a rule seems
  ignored, `claude-route which` says what won.
- **Mirror settings are copies.** Edit the source, then `claude-acs mirror`; editing the
  mirror is undone by the next run.
- **Scheduled jobs** (morning briefing, EOD) run `~/.local/bin/claude` with a fixed
  `CLAUDE_CONFIG_DIR` and bypass the router. Point them at the new account by hand if needed.
