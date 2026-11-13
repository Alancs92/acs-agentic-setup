#!/usr/bin/env python3
"""claude-router tests: fake HOME, fake account store, fake `claude`; nothing live is touched.

Uses this repo's tracked claude_code_toggle.sh, so account switching (config dir plus the
telemetry/privacy env) is the real claude-acs code. Stdlib unittest + zsh + git.
Run: python3 setups/claude-account-router/scripts/test_claude_router.py
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # setups/claude-account-router
REPO = HERE.parent.parent
TOGGLE = REPO / "setups/claude-code-account-profiles/shell/claude_code_toggle.sh"

FAKE_CLAUDE = r"""#!/bin/sh
# Reports what it was started with, as JSON on stdout.
python3 -I - "$@" <<'PY'
import json, os, sys
keys = ["CLAUDE_CONFIG_DIR", "DISABLE_TELEMETRY", "OTEL_SDK_DISABLED", "CLAUDE_ROUTER_SHELL"]
print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(),
                  "env": {k: os.environ.get(k) for k in keys}}))
PY
[ -n "$FAKE_STDIN" ] && cat
exit "${FAKE_EXIT:-0}"
"""


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-router-test-")).resolve()
        self.home = self.tmp / "home"
        self.store = self.home / ".claude-accounts"
        for acct in ("harrison", "personal"):
            (self.store / acct).mkdir(parents=True)
            (self.store / acct / "settings.json").write_text("{}\n")
        (self.store / ".default").write_text("harrison\n")
        shutil.copy(TOGGLE, self.home / "claude_code_toggle.sh")
        self.realbin = self.tmp / "realbin"
        self.realbin.mkdir()
        (self.realbin / "claude").write_text(FAKE_CLAUDE)
        (self.realbin / "claude").chmod(0o755)
        self.rules = self.home / ".config/claude-router/rules"
        self.log = self.home / ".local/state/claude-router/decisions.log"
        self.wrapper_dir = HERE / "bin"
        self.work = self.tmp / "work"
        self.work.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---------------------------------------------------------------
    def base_env(self, **extra):
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.wrapper_dir}:{self.realbin}:/usr/bin:/bin:/opt/homebrew/bin",
            "CLAUDE_ACCOUNT_STORE": str(self.store),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "LANG": "en_US.UTF-8",
        }
        env.update({k: v for k, v in extra.items() if v is not None})
        return env

    def shell_env(self, **extra):
        """What an interactive shell exports after start-up: the default account, marked."""
        return self.base_env(CLAUDE_CONFIG_DIR=str(self.store / "harrison"), CLAUDE_ROUTER_SHELL="1", **extra)

    def write_rules(self, text):
        self.rules.parent.mkdir(parents=True, exist_ok=True)
        self.rules.write_text(textwrap.dedent(text))

    def repo(self, name, remote=None):
        d = self.work / name
        d.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=d)
        if remote:
            git("remote", "add", "origin", remote, cwd=d)
        return d

    def run_claude(self, *args, cwd=None, env=None, stdin=None):
        p = subprocess.run([str(self.wrapper_dir / "claude"), *args], cwd=cwd or self.work,
                           env=env if env is not None else self.shell_env(),
                           capture_output=True, text=True, input=stdin, timeout=30)
        return p

    def launched(self, p):
        self.assertEqual(p.returncode, 0, p.stderr)
        line = p.stdout.splitlines()[0]
        return json.loads(line)

    def account(self, p):
        d = self.launched(p)["env"]["CLAUDE_CONFIG_DIR"]
        return Path(d).name if d else None

    def route(self, *args, cwd=None, env=None):
        return subprocess.run([str(self.wrapper_dir / "claude-route"), *args], cwd=cwd or self.work,
                              env=env if env is not None else self.shell_env(),
                              capture_output=True, text=True, timeout=30)


class Decisions(RouterTestCase):
    def test_no_rules_keeps_shell_default(self):
        self.assertEqual(self.account(self.run_claude()), "harrison")

    def test_remote_rule_switches_and_applies_account_env(self):
        self.write_rules("personal remote=*[:/]Alancs92/*\n")
        r = self.repo("side", "git@github.com:Alancs92/side.git")
        out = self.launched(self.run_claude(cwd=r))
        self.assertEqual(Path(out["env"]["CLAUDE_CONFIG_DIR"]).name, "personal")
        # the real claude-acs privacy opt-outs for a non-org account
        self.assertEqual(out["env"]["DISABLE_TELEMETRY"], "1")
        self.assertEqual(out["env"]["OTEL_SDK_DISABLED"], "true")
        # a claude started from inside this one keeps the account
        self.assertIsNone(out["env"]["CLAUDE_ROUTER_SHELL"])

    def test_remote_and_repo_ignore_case_branch_does_not(self):
        self.write_rules("personal remote=*[:/]Alancs92/*\nharrison branch=FEAT\n")
        r = self.repo("skills", "https://github.com/alancs92/skills.git")
        self.assertEqual(self.account(self.run_claude(cwd=r)), "personal")
        self.write_rules("personal repo=SKILLS\n")
        self.assertEqual(self.account(self.run_claude(cwd=r)), "personal")
        self.write_rules("personal branch=MAIN\n")
        self.assertEqual(self.account(self.run_claude(cwd=r)), "harrison")

    def test_switch_to_org_account_clears_opt_outs(self):
        self.write_rules("harrison repo=work\n")
        r = self.repo("work", "https://github.com/harrison-ai/work.git")
        env = self.base_env(CLAUDE_CONFIG_DIR=str(self.store / "personal"), DISABLE_TELEMETRY="1",
                            OTEL_SDK_DISABLED="true", CLAUDE_ROUTE_ACCOUNT="harrison")
        out = self.launched(self.run_claude(cwd=r, env=env))
        self.assertEqual(Path(out["env"]["CLAUDE_CONFIG_DIR"]).name, "harrison")
        self.assertIsNone(out["env"]["DISABLE_TELEMETRY"])
        self.assertIsNone(out["env"]["OTEL_SDK_DISABLED"])

    def test_explicit_choice_beats_rules(self):
        self.write_rules("harrison\n")  # catch-all
        env = self.base_env(CLAUDE_CONFIG_DIR=str(self.store / "personal"), CLAUDE_ROUTER_SHELL="1")
        self.assertEqual(self.account(self.run_claude(env=env)), "personal")

    def test_default_account_set_by_a_script_is_a_choice(self):
        # no CLAUDE_ROUTER_SHELL: an automation exported the default account on purpose
        self.write_rules("personal\n")
        env = self.base_env(CLAUDE_CONFIG_DIR=str(self.store / "harrison"))
        self.assertEqual(self.account(self.run_claude(env=env)), "harrison")

    def test_custom_config_dir_outside_store_is_kept(self):
        self.write_rules("personal\n")
        custom = self.tmp / "custom-claude"
        custom.mkdir()
        env = self.base_env(CLAUDE_CONFIG_DIR=str(custom), CLAUDE_ROUTER_SHELL="1")
        self.assertEqual(self.launched(self.run_claude(env=env))["env"]["CLAUDE_CONFIG_DIR"], str(custom))

    def test_route_account_env_beats_everything(self):
        self.write_rules("harrison\n")
        env = self.base_env(CLAUDE_CONFIG_DIR=str(self.store / "harrison"), CLAUDE_ROUTE_ACCOUNT="personal")
        self.assertEqual(self.account(self.run_claude(env=env)), "personal")

    def test_unset_config_dir_falls_back_to_default(self):
        # e.g. a process that stripped CLAUDE_CONFIG_DIR and never ran the toggle
        self.assertEqual(self.account(self.run_claude(env=self.base_env())), "harrison")

    def test_orca_managed_dir_is_overridden(self):
        managed = self.home / "Library/Application Support/orca/claude-profiles/abc/home"
        managed.mkdir(parents=True)
        env = self.base_env(CLAUDE_CONFIG_DIR=str(managed))
        self.assertEqual(self.account(self.run_claude(env=env)), "harrison")  # no rule: default
        self.write_rules("personal env:ORCA_PANE_KEY=?*\n")
        env["ORCA_PANE_KEY"] = "pane-1"
        self.assertEqual(self.account(self.run_claude(env=env)), "personal")

    def test_first_match_wins_and_all_conditions_must_hold(self):
        self.write_rules("""
            # comment line

            personal repo=side branch=feat/*   # trailing comment
            harrison repo=side
            personal
        """)
        r = self.repo("side", "https://github.com/x/side.git")
        self.assertEqual(self.account(self.run_claude(cwd=r)), "harrison")   # branch is main
        git("checkout", "-q", "-b", "feat/x", cwd=r)
        self.assertEqual(self.account(self.run_claude(cwd=r)), "personal")
        self.assertEqual(self.account(self.run_claude()), "personal")        # catch-all

    def test_path_rule_tilde_subfolders_and_negation(self):
        (self.home / "repos/side/deep").mkdir(parents=True)
        (self.home / "repos/sidecar").mkdir(parents=True)
        self.write_rules("personal path=~/repos/side !path=~/repos/side/vendored*\n")
        self.assertEqual(self.account(self.run_claude(cwd=self.home / "repos/side/deep")), "personal")
        self.assertEqual(self.account(self.run_claude(cwd=self.home / "repos/side")), "personal")
        # a sibling folder sharing the prefix does not match
        self.assertEqual(self.account(self.run_claude(cwd=self.home / "repos/sidecar")), "harrison")
        (self.home / "repos/side/vendored-x").mkdir()
        self.assertEqual(self.account(self.run_claude(cwd=self.home / "repos/side/vendored-x")), "harrison")

    def test_symlinked_folder_matches_its_real_path(self):
        real = self.home / "repos/side"
        real.mkdir(parents=True)
        link = self.tmp / "link"
        link.symlink_to(real)
        self.write_rules("personal path=~/repos/side\n")
        self.assertEqual(self.account(self.run_claude(cwd=link)), "personal")

    def test_repo_name_from_bare_layout_without_origin(self):
        root = self.work / "proj"
        root.mkdir()
        git("init", "-q", "--bare", ".bare", cwd=root)
        (root / ".git").write_text("gitdir: .bare\n")
        git("-C", ".bare", "config", "core.bare", "true", cwd=root)
        tree = subprocess.run(["git", "-C", str(root / ".bare"), "mktree"], input="", capture_output=True, text=True).stdout.strip()
        commit = subprocess.run(["git", "-C", str(root / ".bare"), "commit-tree", tree, "-m", "init"],
                                capture_output=True, text=True, check=True,
                                env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()
        git("-C", ".bare", "branch", "main", commit, cwd=root)
        git("-C", ".bare", "worktree", "add", "-q", str(root / "main"), "main", cwd=root)
        self.write_rules("personal repo=proj\n")
        self.assertEqual(self.account(self.run_claude(cwd=root / "main")), "personal")

    def test_pin_beats_rules_and_is_shared_by_worktrees(self):
        r = self.repo("side", "https://github.com/x/side.git")
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"], cwd=r, check=True,
                       env={**self.base_env(), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        git("worktree", "add", "-q", "-b", "other", str(self.work / "side-other"), cwd=r)
        self.write_rules("harrison\n")
        p = self.route("pin", "personal", cwd=r)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.account(self.run_claude(cwd=r)), "personal")
        self.assertEqual(self.account(self.run_claude(cwd=self.work / "side-other")), "personal")
        self.assertEqual(self.route("unpin", cwd=r).returncode, 0)
        self.assertEqual(self.account(self.run_claude(cwd=r)), "harrison")


class FailClosed(RouterTestCase):
    def assert_refused(self, p, needle):
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, "", "real claude must not run")
        self.assertIn(needle, p.stderr)

    def test_rule_naming_unsaved_account(self):
        self.write_rules("ghost\n")
        self.assert_refused(self.run_claude(), "'ghost'")

    def test_route_account_unsaved(self):
        self.assert_refused(self.run_claude(env=self.shell_env(CLAUDE_ROUTE_ACCOUNT="ghost")), "'ghost'")

    def test_route_account_path_traversal(self):
        self.assert_refused(self.run_claude(env=self.shell_env(CLAUDE_ROUTE_ACCOUNT="../harrison")), "not saved")

    def test_malformed_rule(self):
        self.write_rules("personal colour=blue\n")
        self.assert_refused(self.run_claude(), "bad condition")

    def test_missing_toggle(self):
        self.write_rules("personal\n")
        (self.home / "claude_code_toggle.sh").unlink()
        self.assert_refused(self.run_claude(), "not found")

    def test_switch_that_does_not_switch(self):
        # a toggle whose claude-acs reports success but leaves the config dir alone
        self.write_rules("personal\n")
        (self.home / "claude_code_toggle.sh").write_text("claude-acs() { return 0; }\n")
        self.assert_refused(self.run_claude(), "switch personal failed")

    def test_no_real_claude(self):
        env = self.shell_env(PATH=f"{self.wrapper_dir}:/usr/bin:/bin")
        p = self.run_claude(env=env)
        self.assertEqual(p.returncode, 127)
        self.assertIn("no real claude", p.stderr)


class Passthrough(RouterTestCase):
    def test_args_exit_code_and_stdin(self):
        args = ["--resume", "abc def", "it's \"quoted\"", "*", "$HOME", ""]
        p = self.run_claude(*args, env=self.shell_env(FAKE_EXIT="7", FAKE_STDIN="1"), stdin="hello\n")
        self.assertEqual(p.returncode, 7)
        out = json.loads(p.stdout.splitlines()[0])
        self.assertEqual(out["args"], args)
        self.assertEqual(p.stdout.splitlines()[1], "hello")

    def test_wrapper_twice_on_path_and_via_symlink(self):
        linkdir = self.tmp / "linkbin"
        linkdir.mkdir()
        (linkdir / "claude").symlink_to(self.wrapper_dir / "claude")
        env = self.shell_env(PATH=f"{linkdir}:{self.wrapper_dir}:{self.realbin}:/usr/bin:/bin")
        p = subprocess.run([str(linkdir / "claude"), "x"], cwd=self.work, env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(p.stdout)["args"], ["x"], p.stderr)

    def test_disable_switch(self):
        self.write_rules("ghost\n")  # would refuse
        p = self.run_claude(env=self.shell_env(CLAUDE_ROUTER_DISABLE="1"))
        self.assertEqual(self.account(p), "harrison")

    def test_log_line_without_arguments(self):
        self.write_rules("personal\n")
        self.run_claude("secret prompt text", env=self.shell_env(ORCA_PANE_KEY="p1", HERDR_SESSION="s"))
        log = self.log.read_text()
        self.assertIn(" personal switch ", log)
        self.assertIn(" orca herdr:s ", log)
        self.assertNotIn("secret", log)


class Cli(RouterTestCase):
    def test_which_explains(self):
        self.write_rules("personal repo=side\n")
        r = self.repo("side", "https://github.com/x/side.git")
        p = self.route("which", str(r))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("account: personal", p.stdout)
        self.assertIn("rule line 1", p.stdout)
        self.assertIn("repo:    side", p.stdout)

    def test_check(self):
        self.write_rules("personal repo=x\nghost\npersonal bogus=1\n")
        p = self.route("check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("line 2: account 'ghost'", p.stdout)
        self.assertIn("line 3: bad condition", p.stdout)
        self.write_rules("personal repo=x\n")
        self.assertEqual(self.route("check").returncode, 0)

    def test_run(self):
        p = subprocess.run([str(self.wrapper_dir / "claude-route"), "run", "personal", "-p", "hi"],
                           cwd=self.work, env=self.shell_env(), capture_output=True, text=True, timeout=30)
        out = self.launched(p)
        self.assertEqual(Path(out["env"]["CLAUDE_CONFIG_DIR"]).name, "personal")
        self.assertEqual(out["args"], ["-p", "hi"])

    def test_pin_rejects_unsaved_account(self):
        r = self.repo("side")
        self.assertEqual(self.route("pin", "ghost", cwd=r).returncode, 1)


class InstalledShell(RouterTestCase):
    """install.sh into the fake HOME, then real interactive zsh start-ups."""

    def setUp(self):
        super().setUp()
        (self.home / ".zshrc").write_text(
            "source ~/claude_code_toggle.sh\n"
            f"path=({self.realbin} $path)\n")

    def install(self):
        p = subprocess.run(["bash", str(HERE / "scripts/install.sh")], env=self.base_env(),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def zsh(self, script, cwd=None, **extra):
        env = self.base_env(**extra)
        env.pop("CLAUDE_ACCOUNT_STORE")  # the toggle sets it, as in a real shell
        p = subprocess.run(["zsh", "-i", "-c", script], cwd=cwd or self.work, env=env,
                           capture_output=True, text=True, timeout=60)
        return p

    def test_install_idempotent_and_keeps_rules(self):
        out = self.install()
        self.assertIn("block appended", out)
        self.assertIn("created", out)
        self.rules.write_text("personal\n")
        out = self.install()
        self.assertIn("block up to date", out)
        self.assertIn("(kept)", out)
        self.assertEqual(self.rules.read_text(), "personal\n")
        self.assertEqual((self.home / ".zshrc").read_text().count("# >>> claude-router >>>"), 1)

    def test_install_replaces_changed_block_in_place(self):
        self.install()
        z = self.home / ".zshrc"
        z.write_text(z.read_text().replace("export CLAUDE_ROUTER_SHELL=1", "export CLAUDE_ROUTER_SHELL=old"))
        z.write_text(z.read_text() + "echo after-block\n")
        self.assertIn("block updated", self.install())
        text = z.read_text()
        self.assertNotIn("=old", text)
        self.assertTrue(text.rstrip().endswith("echo after-block"))

    def test_shell_routes_and_manual_switch_wins(self):
        self.install()
        self.rules.write_text("personal repo=side\n")
        r = self.repo("side", "https://github.com/x/side.git")
        p = self.zsh("claude --resume abc", cwd=r)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(Path(json.loads(p.stdout.splitlines()[-1])["env"]["CLAUDE_CONFIG_DIR"]).name, "personal")
        # an explicit switch to the default account in that shell beats the rule
        p = self.zsh("claude-acs sw harrison >/dev/null; claude", cwd=r)
        self.assertEqual(Path(json.loads(p.stdout.splitlines()[-1])["env"]["CLAUDE_CONFIG_DIR"]).name, "harrison", p.stderr)
        # a failed switch leaves the shell un-chosen
        p = self.zsh("claude-acs sw ghost >/dev/null; claude", cwd=r)
        self.assertEqual(Path(json.loads(p.stdout.splitlines()[-1])["env"]["CLAUDE_CONFIG_DIR"]).name, "personal", p.stderr)

    def test_router_first_on_path(self):
        self.install()
        p = self.zsh("whence -p claude; whence -p claude-route")
        lines = p.stdout.split()
        self.assertEqual(lines[0], str(self.home / ".local/share/claude-router/bin/claude"), p.stderr)

    def test_herdr_routed_session_is_a_choice(self):
        # herdr-cmux block ran `claude-acs switch personal` before this block
        self.install()
        z = self.home / ".zshrc"
        text = z.read_text().replace("source ~/claude_code_toggle.sh\n",
                                     "source ~/claude_code_toggle.sh\nclaude-acs switch personal >/dev/null\n")
        z.write_text(text)
        self.rules.write_text("harrison\n")
        p = self.zsh("claude")
        self.assertEqual(Path(json.loads(p.stdout.splitlines()[-1])["env"]["CLAUDE_CONFIG_DIR"]).name, "personal", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
