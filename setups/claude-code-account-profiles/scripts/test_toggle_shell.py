#!/usr/bin/env python3
"""Tests for the tracked copy of claude_code_toggle.sh.

The live file stays at ~/claude_code_toggle.sh -- .zshrc sources it there and it
is used constantly, so it is deliberately NOT relocated. This directory keeps a
tracked copy so the file has history and a recoverable version, and these tests
guard the two ways it has actually broken.

Stdlib unittest + zsh. Run: python3 test_toggle_shell.py
"""
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKED = os.path.join(HERE, os.pardir, "shell", "claude_code_toggle.sh")
LIVE = os.path.expanduser("~/claude_code_toggle.sh")
REL = "demo-setup/scripts/thing.py"

# Every claude-acs helper name the file mentions, defined or called.
_HELPER = re.compile(r"\b(_{1,2}claude_acs_[A-Za-z0-9_]+)")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def defined_functions(path):
    """Function names defined after sourcing `path` in a clean zsh."""
    result = subprocess.run(
        ["zsh", "-c", "source %s >/dev/null 2>&1; print -l ${(ok)functions}" % path],
        capture_output=True, text=True,
    )
    return set(result.stdout.split())


class ToggleSourcesCleanly(unittest.TestCase):

    def test_sourcing_defines_the_public_entry_point(self):
        self.assertIn("claude-acs", defined_functions(TRACKED))

    def test_every_helper_it_references_actually_exists(self):
        """No dispatch target may be missing.

        `claude-acs <cmd>` dispatches to a helper by name. A helper that is
        renamed, typo'd or lost leaves the dispatcher intact and fails only when
        that one subcommand is run -- which is how `claude-acs backup` came to
        report "command not found: _claude_acs_backup".
        """
        defined = defined_functions(TRACKED)
        referenced = set(_HELPER.findall(read(TRACKED)))
        self.assertTrue(referenced, "no helpers found -- the regex or file moved")
        missing = sorted(name for name in referenced if name not in defined)
        self.assertEqual(missing, [], "helpers referenced but never defined: %s" % missing)


class ToggleSurvivesShellSnapshots(unittest.TestCase):

    def test_helpers_are_double_underscored(self):
        """Single-underscore helpers are stripped from Claude Code shell snapshots.

        Claude Code snapshots the shell to reproduce it for non-interactive tool
        calls, and drops single-underscore function names -- the convention zsh
        completions use, roughly a thousand of them. Helpers named
        `_claude_acs_*` were therefore absent inside Claude Code while the public
        `claude-acs` wrapper survived, so every subcommand failed there and
        nowhere else. Double-underscore names are kept.
        """
        offenders = sorted(
            name for name in set(_HELPER.findall(read(TRACKED)))
            if not name.startswith("__")
        )
        self.assertEqual(
            offenders, [],
            "single-underscore helpers will be stripped from shell snapshots: %s"
            % offenders,
        )


class WorktreeResolution(unittest.TestCase):
    """Which worktree `claude-acs` runs code from, when several could serve.

    The bare-repo layout puts every worktree beside its siblings, so a glob finds
    the same setups/ path in all of them. Picking the first sorted match is
    deterministic but arbitrary: it silently stops being the branch you think the
    moment a worktree is created that sorts ahead of it.
    """

    def _repo(self, tmp, worktrees, default_branch="main"):
        """Build repo/.bare plus worktrees, each a git repo on a named branch."""
        repo = os.path.join(tmp, "repo")
        bare = os.path.join(repo, ".bare")
        os.makedirs(bare)
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True)
        subprocess.run(
            ["git", "-C", bare, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/heads/%s" % default_branch], check=True)
        for name, branch in worktrees:
            wt = os.path.join(repo, name)
            script = os.path.join(wt, "setups", REL)
            os.makedirs(os.path.dirname(script))
            open(script, "w").close()
            subprocess.run(["git", "init", "-q", wt], check=True)
            subprocess.run(
                ["git", "-C", wt, "symbolic-ref", "HEAD", "refs/heads/%s" % branch],
                check=True)
        return repo

    def _resolve(self, repo):
        result = subprocess.run(
            ["zsh", "-c",
             "source %s >/dev/null 2>&1; __claude_acs_setup_script %s" % (TRACKED, REL)],
            capture_output=True, text=True, env=dict(os.environ, CLAUDE_ACS_REPO=repo),
        )
        return result.stdout.strip(), result.stderr.strip()

    def test_prefers_the_default_branch_over_the_first_sorted_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, [("aaa-other", "feat/other"), ("zzz-main", "main")])
            out, _ = self._resolve(repo)
            self.assertIn("zzz-main", out)
            self.assertNotIn("aaa-other", out)

    def test_falls_back_to_first_sorted_but_says_so(self):
        """No worktree on the default branch is a real state, not an error.

        It must still resolve -- refusing would break a working setup -- but
        silently running an arbitrary branch's code is how this bites, so the
        fallback announces itself on stderr.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, [("aaa-other", "feat/other"), ("bbb-third", "feat/third")])
            out, err = self._resolve(repo)
            self.assertIn("aaa-other", out)
            self.assertIn("falling back", err)

    def test_single_worktree_resolves_without_complaint(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, [("only", "main")])
            out, err = self._resolve(repo)
            self.assertIn("only", out)
            self.assertEqual(err, "")


class RegroupLaunch(unittest.TestCase):
    """`claude-acs regroup` launches /regroup with the right env and records the session."""

    def _run(self, *args, account="harrison", make_account=True):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        store = os.path.join(tmp, "store")
        if make_account:
            os.makedirs(os.path.join(store, account))
        stub = os.path.join(tmp, "claude-stub")
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\necho "CFG=$CLAUDE_CONFIG_DIR"\necho "PWD=$(pwd -P)"\n'
                     'for a in "$@"; do echo "ARG=$a"; done\n')
        os.chmod(stub, 0o755)
        env = dict(os.environ, CLAUDE_ACS_CLAUDE_BIN=stub,
                   CLAUDE_ACS_REGROUP_DIR=os.path.join(tmp, "regroup"),
                   CLAUDE_ACS_REGROUP_ACCOUNT=account, HOME=tmp)
        env.pop("CLAUDE_CONFIG_DIR", None)
        cmd = ("source %s >/dev/null 2>&1; CLAUDE_ACCOUNT_STORE=%s; "
               "claude-acs regroup %s; echo RC=$?; echo AFTER_CFG=${CLAUDE_CONFIG_DIR:-unset}"
               % (TRACKED, store, " ".join(args)))
        res = subprocess.run(["zsh", "-c", cmd], capture_output=True, text=True, env=env)
        return res, tmp, store

    def test_launches_regroup_with_session_id_config_dir_and_skip_entry(self):
        res, tmp, store = self._run("now")
        lines = res.stdout.splitlines()
        self.assertIn("CFG=%s" % os.path.join(store, "harrison"), lines)
        self.assertIn("PWD=%s" % os.path.realpath(tmp), lines)
        self.assertIn("ARG=/regroup now", lines)
        self.assertIn("ARG=sonnet", lines)
        self.assertIn("ARG=--model", lines)
        sid = lines[lines.index("ARG=--session-id") + 1][len("ARG="):]
        self.assertRegex(sid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertEqual(read(os.path.join(tmp, "regroup", "skip-sessions")).split(), [sid])
        self.assertIn("AFTER_CFG=unset", lines)  # subshell-scoped

    def test_rg_alias_dispatches(self):
        res, _, _ = self._run()
        self.assertIn("ARG=/regroup ", res.stdout)

    def test_unknown_account_fails_before_launching(self):
        res, tmp, _ = self._run(make_account=False)
        self.assertNotIn("ARG=", res.stdout)
        self.assertIn("not found", res.stdout)
        self.assertFalse(os.path.exists(os.path.join(tmp, "regroup", "skip-sessions")))

    def test_help_lists_regroup(self):
        out = subprocess.run(["zsh", "-c", "source %s >/dev/null 2>&1; claude-acs help" % TRACKED],
                             capture_output=True, text=True).stdout
        self.assertIn("regroup", out)


def zsh_isolated(script, tmp, new_session=False, **env_extra):
    """Run `script` after sourcing the tracked toggle in zsh with HOME=tmp.

    new_session puts zsh in its own process group, so a test can hang up the
    whole job (as closing the terminal does) without signalling the runner.
    """
    env = dict(os.environ, HOME=tmp, **env_extra)
    for var in ("CLAUDE_CONFIG_DIR", "CLAUDE_OTEL_ORG_ACCOUNTS", "OTEL_SDK_DISABLED",
                "DISABLE_TELEMETRY", "DISCORD_BOT_TOKEN"):
        if var not in env_extra:
            env.pop(var, None)
    return subprocess.run(["zsh", "-c", "source %s >/dev/null 2>&1; %s" % (TRACKED, script)],
                          capture_output=True, text=True, env=env, start_new_session=new_session)


class OrgAccountTelemetry(unittest.TestCase):
    """Which accounts get telemetry by default when switched to.

    zsh does not word-split an unquoted $VAR, so `for org in $LIST` sees one word.
    That held while the list was just "harrison"; with two names neither matched.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _is_org(self, name, **env):
        res = zsh_isolated("_claude_otel_is_org_account %s; echo RC=$?" % name, self.tmp, **env)
        return "RC=0" in res.stdout

    def test_default_list_covers_harrison_and_hai_enterprise(self):
        self.assertTrue(self._is_org("harrison"))
        self.assertTrue(self._is_org("hai-enterprise"))
        self.assertFalse(self._is_org("personal"))
        self.assertFalse(self._is_org("hai"))  # whole names only

    def test_every_name_in_a_custom_list_matches(self):
        env = {"CLAUDE_OTEL_ORG_ACCOUNTS": "alpha beta"}
        self.assertTrue(self._is_org("alpha", **env))
        self.assertTrue(self._is_org("beta", **env))
        self.assertFalse(self._is_org("gamma", **env))

    def test_switch_default_leaves_org_telemetry_on_and_turns_others_off(self):
        probe = ("_claude_otel_apply_account_default %s; "
                 "echo SDK=${OTEL_SDK_DISABLED:-unset} DT=${DISABLE_TELEMETRY:-unset}")
        org = zsh_isolated(probe % "hai-enterprise", self.tmp).stdout
        other = zsh_isolated(probe % "personal", self.tmp).stdout
        self.assertIn("SDK=unset DT=unset", org)
        self.assertIn("SDK=true DT=1", other)


class DiscordLaunch(unittest.TestCase):
    """`claude-acs discord` starts Claude with the Discord channel plugin.

    The bot token comes from 1Password and sits in the plugin's .env only while
    Claude runs; it is never exported, so Claude's Bash commands cannot see it.
    The 1Password account and item come from a private file in the account store,
    so the public repo never names them. `op` and `claude` are stubs here.
    """

    TOKEN = "tok-test-123"
    OP_ACCOUNT = "ACCT-TEST"
    OP_REF = "op://Vault/Item Name/token field"  # spaces: must stay one argument

    def _run(self, *args, make_account=True, make_conf=True, op_fails=False, stub_rc=0,
             stale_env=False, kill_with=None, ctrl_c=False):
        """stale_env leaves a world-readable .env behind first. hangup does what
        closing the terminal does mid-session: the interactive shell dies (so
        nothing after the launch runs) and the job gets SIGHUP. kill_with="TERM"
        does the same with SIGTERM. ctrl_c sends SIGINT mid-session to Claude
        (which handles it, as it does to cancel a turn) and to the shell around it."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        store = os.path.join(tmp, "store")
        if make_account:
            os.makedirs(os.path.join(store, "personal"))
        if make_conf:
            os.makedirs(store, exist_ok=True)
            with open(os.path.join(store, ".discord-op"), "w") as fh:
                fh.write("CLAUDE_ACS_DISCORD_OP_ACCOUNT=%s\nCLAUDE_ACS_DISCORD_OP_REF='%s'\n"
                         % (self.OP_ACCOUNT, self.OP_REF))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        oplog = os.path.join(tmp, "op.log")
        op = os.path.join(bindir, "op")
        with open(op, "w") as fh:
            fh.write('#!/bin/sh\nfor a in "$@"; do echo "$a"; done >> %s\n%s\n'
                     % (oplog, "exit 1" if op_fails else "echo %s" % self.TOKEN))
        stub = os.path.join(tmp, "claude-stub")
        envfile = os.path.join(tmp, "discord", ".env")
        if stale_env:
            os.makedirs(os.path.dirname(envfile))
            with open(envfile, "w") as fh:
                fh.write("STALE=1\n")
            os.chmod(envfile, 0o644)
        if kill_with:
            ending = 'kill -KILL "$OUTER"; kill -%s "$PPID"; kill -%s $$\nsleep 5\n' % (kill_with, kill_with)
        elif ctrl_c:
            ending = ("trap 'echo STUB_GOT_INT' INT\nkill -INT \"$PPID\" $$\nsleep 1\n"
                      "[ -e %s ] && echo ENV_DURING=present || echo ENV_DURING=missing\nexit 0\n" % envfile)
        else:
            ending = "exit %d\n" % stub_rc
        with open(stub, "w") as fh:
            fh.write('#!/bin/sh\necho "CFG=$CLAUDE_CONFIG_DIR"\n'
                     'for a in "$@"; do echo "ARG=$a"; done\n'
                     'echo "ENV=$(cat %s)"\n'
                     'echo "MODE=$(stat -f %%Lp %s 2>/dev/null || stat -c %%a %s)"\n'
                     'echo "CHILD_TOKEN=${DISCORD_BOT_TOKEN:-unset}"\n'
                     'echo "UMASK=$(umask)"\n'
                     'echo "CHILD_INT_IGNORED=$(python3 -I -c \'import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)\')"\n'
                     % (envfile, envfile, envfile) + ending)
        os.chmod(op, 0o755)
        os.chmod(stub, 0o755)
        cmd = ("export OUTER=$$; umask 022; CLAUDE_ACCOUNT_STORE=%s; claude-acs %s; echo RC=$?; "
               "echo AFTER_CFG=${CLAUDE_CONFIG_DIR:-unset}; "
               "echo AFTER_OP=${CLAUDE_ACS_DISCORD_OP_ACCOUNT:-unset}; "
               "[ -e %s ] && echo ENV_LEFT || echo ENV_GONE"
               % (store, " ".join(args) or "discord", envfile))
        res = zsh_isolated(cmd, tmp, new_session=bool(kill_with), CLAUDE_ACS_CLAUDE_BIN=stub,
                           CLAUDE_ACS_DISCORD_DIR=os.path.dirname(envfile),
                           PATH=bindir + os.pathsep + os.environ["PATH"])
        oplines = read(oplog).splitlines() if os.path.exists(oplog) else None
        self.env_left = os.path.exists(envfile)
        return res, store, oplines

    def test_launches_with_the_channel_flag_config_dir_and_args(self):
        res, store, _ = self._run("discord", "--resume")
        lines = res.stdout.splitlines()
        self.assertIn("CFG=%s" % os.path.join(store, "personal"), lines)
        i = lines.index("ARG=--channels")
        self.assertEqual(lines[i + 1:i + 3], ["ARG=plugin:discord@claude-plugins-official",
                                              "ARG=--resume"])
        self.assertIn("AFTER_CFG=unset", lines)  # subshell-scoped
        self.assertIn("RC=0", lines)
        self.assertIn("UMASK=022", [l.replace("UMASK=0", "UMASK=") for l in lines])  # .env's 077 stays on .env
        self.assertIn("CHILD_INT_IGNORED=False", lines)  # Ctrl-C still reaches Claude

    def test_token_lives_in_a_private_env_file_only_while_running(self):
        res, _, oplines = self._run()
        lines = res.stdout.splitlines()
        self.assertIn("ENV=DISCORD_BOT_TOKEN=%s" % self.TOKEN, lines)
        self.assertIn("MODE=600", lines)
        self.assertIn("CHILD_TOKEN=unset", lines)  # not exported to Claude
        self.assertIn("ENV_GONE", lines)
        self.assertNotIn(self.TOKEN, res.stderr)
        self.assertEqual(oplines, ["read", "--account", self.OP_ACCOUNT, self.OP_REF])
        self.assertIn("AFTER_OP=unset", lines)  # private settings stay out of the shell

    def test_exit_code_passes_through_and_the_file_is_still_removed(self):
        res, _, _ = self._run(stub_rc=3)
        self.assertIn("RC=3", res.stdout.splitlines())
        self.assertIn("ENV_GONE", res.stdout.splitlines())

    def test_a_stale_env_file_is_replaced_not_reused(self):
        """umask only applies to new files: writing into an old 644 .env keeps 644."""
        res, _, _ = self._run(stale_env=True)
        lines = res.stdout.splitlines()
        self.assertIn("MODE=600", lines)
        self.assertIn("ENV=DISCORD_BOT_TOKEN=%s" % self.TOKEN, lines)
        self.assertFalse(self.env_left)

    def test_closing_the_terminal_mid_session_still_removes_the_token(self):
        res, _, _ = self._run(kill_with="HUP")
        self.assertIn("ENV=DISCORD_BOT_TOKEN=%s" % self.TOKEN, res.stdout.splitlines())
        self.assertFalse(self.env_left, "the .env with the token survived a hangup")

    def test_ctrl_c_in_claude_keeps_the_session_and_still_cleans_up(self):
        """Ctrl-C cancels a turn in Claude. The shell around it must keep waiting:
        a zsh subshell interrupted while waiting exits without its EXIT trap."""
        res, _, _ = self._run(ctrl_c=True)
        lines = res.stdout.splitlines()
        self.assertIn("STUB_GOT_INT", lines)
        self.assertIn("ENV_DURING=present", lines)
        self.assertIn("RC=0", lines)
        self.assertIn("ENV_GONE", lines)
        self.assertFalse(self.env_left)

    def test_a_terminate_signal_still_removes_the_token(self):
        res, _, _ = self._run(kill_with="TERM")
        self.assertIn("ENV=DISCORD_BOT_TOKEN=%s" % self.TOKEN, res.stdout.splitlines())
        self.assertFalse(self.env_left, "the .env with the token survived SIGTERM")

    def test_a_failed_token_read_stops_before_launching(self):
        res, _, _ = self._run(op_fails=True)
        self.assertNotIn("ARG=", res.stdout)
        self.assertNotIn("RC=0", res.stdout.splitlines())
        self.assertIn("ENV_GONE", res.stdout.splitlines())

    def test_unknown_account_fails_before_reading_the_token(self):
        res, _, oplines = self._run(make_account=False)
        self.assertNotIn("ARG=", res.stdout)
        self.assertIn("not found", res.stdout)
        self.assertIsNone(oplines)

    def test_missing_private_settings_fail_before_reading_the_token(self):
        res, _, oplines = self._run(make_conf=False)
        self.assertNotIn("ARG=", res.stdout)
        self.assertIn(".discord-op", res.stdout)
        self.assertIsNone(oplines)

    def test_dc_alias_dispatches(self):
        res, _, _ = self._run("dc")
        self.assertIn("ARG=--channels", res.stdout.splitlines())

    def test_help_lists_discord(self):
        out = subprocess.run(["zsh", "-c", "source %s >/dev/null 2>&1; claude-acs help" % TRACKED],
                             capture_output=True, text=True).stdout
        self.assertIn("discord, dc", out)


class NoPrivateIdentifiers(unittest.TestCase):

    def test_no_1password_account_or_item_in_the_tracked_copy(self):
        """The repo is public: 1Password ids live in the account store, not here."""
        text = read(TRACKED)
        self.assertNotIn("op://", text)
        self.assertIsNone(re.search(r"--account\s+[A-Z0-9]{20,}", text))


class TrackedCopyMatchesLive(unittest.TestCase):

    def test_tracked_copy_is_current(self):
        """The copy is only useful if it is not stale.

        Skipped where the live file does not exist (CI, another machine), so this
        asserts freshness without making the suite machine-dependent.
        """
        if not os.path.isfile(LIVE):
            self.skipTest("no live ~/claude_code_toggle.sh on this machine")
        if os.environ.get("ACS_SKIP_LIVE_DRIFT"):
            self.skipTest("ACS_SKIP_LIVE_DRIFT set (pre-cutover: live copy not yet refreshed)")
        self.assertEqual(
            read(TRACKED), read(LIVE),
            "tracked copy has drifted from the live file; refresh it with:\n"
            "  cp ~/claude_code_toggle.sh %s" % os.path.normpath(TRACKED),
        )


if __name__ == "__main__":
    unittest.main()
