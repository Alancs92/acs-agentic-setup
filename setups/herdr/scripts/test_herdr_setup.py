#!/usr/bin/env python3
"""Tests for the herdr setup: zsh block, install.sh, capture.sh, acs.attention.

Every test runs against a throwaway HOME with fake `herdr` / `cmux` binaries on
PATH, so nothing touches the live machine. Stdlib unittest + zsh + bash.

Run: python3 setups/herdr/scripts/test_herdr_setup.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.dirname(HERE)
ZSH_BLOCK = os.path.join(SETUP, "config", "zsh", "herdr-cmux.zsh")
ATTENTION = os.path.join(SETUP, "config", "herdr", "plugins", "attention", "attention.py")
BEGIN = "# >>> herdr-cmux >>>"
END = "# <<< herdr-cmux <<<"
LEGACY_BLOCK = textwrap.dedent("""\
    # herdr inside cmux: hide cmux's surface IDs from herdr panes so cmux's Claude hooks
    # don't record herdr's sessions against the hosting tab.
    if [[ -n $HERDR_ENV && -n $CMUX_SURFACE_ID ]]; then
      export HERDR_HOST_CMUX_SURFACE=$CMUX_SURFACE_ID
      unset CMUX_SURFACE_ID CMUX_PANEL_ID CMUX_TAB_ID CMUX_WORKSPACE_ID
    fi
""")

FAKE_HERDR = """#!/bin/bash
echo "herdr $* [${CLAUDE_CONFIG_DIR:-}]" >> "$FAKE_LOG"
case "$1 $2" in
  "integration status")
    if [ -f "$CLAUDE_CONFIG_DIR/integrated" ]; then echo "claude: current (v10)"; else echo "claude: not installed"; fi
    # Like the real herdr, keep writing after the claude line: a reader that stops
    # early (grep -q) makes these writes fail with SIGPIPE.
    for _ in $(seq 1 2000); do echo "other: not installed (/some/long/path/to/an/integration/file)"; done ;;
  "integration install") touch "$CLAUDE_CONFIG_DIR/integrated" ;;
  "session list")
    [ -n "${FAKE_SESSION_FAIL:-}" ] && exit 1
    printf 'name status directory socket\\ndefault stopped /a /b\\nfoo running /c /d\\n' ;;
  "--session foo") [ -n "${FAKE_RELOAD_FAIL:-}" ] && exit 1; exit 0 ;;
  *) exit 0 ;;
esac
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Sandbox(unittest.TestCase):
    """A fake HOME with saved accounts, a fake herdr, and a private copy of the setup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="herdr-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        self.store = os.path.join(self.home, ".claude-accounts")
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "herdr.log")
        self.setup_copy = os.path.join(self.tmp, "setup")
        shutil.copytree(SETUP, self.setup_copy)
        for acct in ("harrison", "personal", "personal2"):
            write(os.path.join(self.store, acct, "settings.json"), "{}\n")
        os.makedirs(os.path.join(self.store, "bin"))  # not an account: no settings.json
        write(os.path.join(self.home, ".zshrc"), "export FOO=1\n")
        write(os.path.join(self.bin, "herdr"), FAKE_HERDR)
        os.chmod(os.path.join(self.bin, "herdr"), 0o755)

    def env(self, **extra):
        env = {
            "HOME": self.home,
            "PATH": self.bin + os.pathsep + "/usr/bin:/bin:/usr/sbin:/sbin",
            "CLAUDE_ACCOUNT_STORE": self.store,
            "FAKE_LOG": self.log,
        }
        env.update(extra)
        return env

    def script(self, name):
        return os.path.join(self.setup_copy, "scripts", name)

    def install(self, *accounts, **extra):
        return subprocess.run(["/bin/bash", self.script("install.sh"), *accounts],
                              capture_output=True, text=True, env=self.env(**extra))

    def snapshot(self):
        out = {}
        for root, _, files in os.walk(self.home):
            for name in files:
                path = os.path.join(root, name)
                out[os.path.relpath(path, self.home)] = read(path)
        return out


class SessionAccountRouting(Sandbox):
    """The zsh block switches a herdr pane's shell to the account its session names."""

    def resolve(self, session):
        cmd = 'source %s; _herdr_session_account %s %s' % (ZSH_BLOCK, session, self.store)
        return subprocess.run(["zsh", "-fc", cmd], capture_output=True, text=True,
                              env=self.env()).stdout.strip()

    def test_session_named_after_an_account_routes_to_it(self):
        self.assertEqual(self.resolve("personal"), "personal")

    def test_dash_account_suffix_routes_to_it(self):
        self.assertEqual(self.resolve("management-personal"), "personal")

    def test_longest_matching_account_wins(self):
        self.assertEqual(self.resolve("research-personal2"), "personal2")

    def test_unmatched_session_keeps_the_default(self):
        for session in ("annalise-v2", "Frontier", "personalx", "xpersonal", "tools-bin"):
            with self.subTest(session=session):
                self.assertEqual(self.resolve(session), "")

    def shell_start(self, session, current="harrison", with_acs=True):
        """Source the block the way ~/.zshrc does, with a recording claude-acs stub."""
        # The block silences claude-acs's stdout, so the stub reports on stderr.
        stub = ('claude-acs() { print -r -- "called: $*" >&2; }; CLAUDE_ACCOUNT=%s; ' % current
                if with_acs else "")
        cmd = stub + "source %s; print -r -- cmux=${CMUX_SURFACE_ID:-}" % ZSH_BLOCK
        env = self.env(HERDR_ENV="1", HERDR_SESSION=session, CMUX_SURFACE_ID="surf")
        return subprocess.run(["zsh", "-fc", cmd], capture_output=True, text=True, env=env)

    def test_shell_start_switches_to_the_routed_account(self):
        result = self.shell_start("management-personal")
        self.assertIn("called: switch personal", result.stderr)

    def test_shell_start_leaves_unmatched_sessions_alone(self):
        self.assertNotIn("called:", self.shell_start("annalise-v2").stderr)

    def test_no_switch_when_already_on_that_account(self):
        self.assertNotIn("called:", self.shell_start("personal", current="personal").stderr)

    def test_without_claude_acs_the_block_is_silent(self):
        result = self.shell_start("personal", with_acs=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_cmux_guard_still_hides_the_surface_id(self):
        self.assertIn("cmux=\n", self.shell_start("annalise-v2").stdout + "\n")

    def test_with_the_real_toggle_the_pane_runs_claude_as_the_routed_account(self):
        """End to end with the tracked claude_code_toggle.sh, sourced as ~/.zshrc does."""
        toggle = os.path.join(os.path.dirname(SETUP), "claude-code-account-profiles",
                              "shell", "claude_code_toggle.sh")
        write(os.path.join(self.store, ".default"), "harrison\n")
        cmd = ("source %s >/dev/null; source %s; print -r -- \"$CLAUDE_ACCOUNT $CLAUDE_CONFIG_DIR\""
               % (toggle, ZSH_BLOCK))
        for session, acct in (("management-personal", "personal"), ("annalise-v2", "harrison")):
            with self.subTest(session=session):
                env = self.env(HERDR_ENV="1", HERDR_SESSION=session)
                out = subprocess.run(["zsh", "-fc", cmd], capture_output=True, text=True,
                                     env=env).stdout.strip()
                self.assertEqual(out, "%s %s" % (acct, os.path.join(self.store, acct)))

    def test_block_carries_managed_markers(self):
        lines = read(ZSH_BLOCK).splitlines()
        self.assertTrue(lines[0].startswith(BEGIN))
        self.assertEqual(lines[-1], END)

    def test_block_names_no_account(self):
        self.assertNotIn("harrison", read(ZSH_BLOCK))


class InstallScript(Sandbox):

    def test_default_installs_every_saved_account(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for acct in ("harrison", "personal", "personal2"):
            hook = os.path.join(self.store, acct, "hooks", "herdr-cmux-notify.sh")
            self.assertTrue(os.path.isfile(hook), acct)
            self.assertIn("herdr-cmux-notify.sh", read(os.path.join(self.store, acct, "settings.json")))
        self.assertFalse(os.path.exists(os.path.join(self.store, "bin", "hooks")))

    def test_installs_herdr_integration_per_account_once(self):
        self.install()
        self.install()
        installs = [l for l in read(self.log).splitlines() if l.startswith("herdr integration install")]
        self.assertEqual(sorted(installs), sorted(
            "herdr integration install claude [%s]" % os.path.join(self.store, a)
            for a in ("harrison", "personal", "personal2")))

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.install().returncode, 0)
        before = self.snapshot()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_account_without_settings_is_skipped_not_fatal(self):
        os.makedirs(os.path.join(self.store, "fresh"))
        result = self.install("fresh", "personal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip account fresh", result.stderr)
        self.assertIn("herdr-cmux-notify.sh", read(os.path.join(self.store, "personal", "settings.json")))
        self.assertIn(BEGIN, read(os.path.join(self.home, ".zshrc")))

    def test_invalid_settings_fails_but_finishes_the_rest(self):
        write(os.path.join(self.store, "harrison", "settings.json"), "{not json")
        result = self.install("harrison", "personal")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("harrison", result.stderr)
        self.assertIn("herdr-cmux-notify.sh", read(os.path.join(self.store, "personal", "settings.json")))
        self.assertIn(BEGIN, read(os.path.join(self.home, ".zshrc")))

    def test_non_ascii_settings_stay_readable(self):
        write(os.path.join(self.store, "personal", "settings.json"), '{"note": "café"}\n')
        self.install("personal")
        self.assertIn("café", read(os.path.join(self.store, "personal", "settings.json")))

    def test_appends_block_once_after_existing_content(self):
        self.install()
        self.install()
        zshrc = read(os.path.join(self.home, ".zshrc"))
        self.assertTrue(zshrc.startswith("export FOO=1\n"))
        self.assertEqual(zshrc.count(BEGIN), 1)

    def test_legacy_block_is_replaced_in_place(self):
        write(os.path.join(self.home, ".zshrc"), "before\n" + LEGACY_BLOCK + "after\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        zshrc = read(os.path.join(self.home, ".zshrc"))
        self.assertNotIn("# herdr inside cmux:", zshrc.replace(read(ZSH_BLOCK), ""))
        self.assertEqual(zshrc, "before\n" + read(ZSH_BLOCK) + "after\n")

    def test_repo_edits_to_the_block_reach_zshrc(self):
        self.install()
        installed = read(os.path.join(self.home, ".zshrc"))
        for name in os.listdir(self.home):
            if name.startswith(".zshrc.bak-"):
                os.remove(os.path.join(self.home, name))
        block = os.path.join(self.setup_copy, "config", "zsh", "herdr-cmux.zsh")
        write(block, read(block).replace(END, "# new line\n" + END))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        zshrc = read(os.path.join(self.home, ".zshrc"))
        self.assertIn("# new line", zshrc)
        self.assertEqual(zshrc.count(BEGIN), 1)
        backups = [n for n in os.listdir(self.home) if n.startswith(".zshrc.bak-")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(read(os.path.join(self.home, backups[0])), installed)

    def test_unterminated_managed_block_is_refused(self):
        original = "x\n" + BEGIN + "\nhalf a block\n"
        write(os.path.join(self.home, ".zshrc"), original)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(read(os.path.join(self.home, ".zshrc")), original)

    def test_session_list_failure_is_a_warning(self):
        result = self.install(FAKE_SESSION_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning", result.stderr)

    def test_reload_failure_is_a_warning(self):
        result = self.install(FAKE_RELOAD_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("foo", result.stderr)

    def test_install_script_names_no_account(self):
        self.assertNotIn("harrison", read(self.script("install.sh")))


class CaptureScript(Sandbox):

    def test_captures_hook_from_any_account_and_the_managed_block(self):
        self.install("personal")
        live_hook = os.path.join(self.store, "personal", "hooks", "herdr-cmux-notify.sh")
        write(live_hook, read(live_hook) + "# live edit\n")
        block = os.path.join(self.setup_copy, "config", "zsh", "herdr-cmux.zsh")
        expected_block = read(block)
        os.remove(block)
        result = subprocess.run(["/bin/bash", self.script("capture.sh")],
                                capture_output=True, text=True, env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        repo_hook = os.path.join(self.setup_copy, "config", "claude-hooks", "herdr-cmux-notify.sh")
        self.assertIn("# live edit", read(repo_hook))
        self.assertEqual(read(block), expected_block)

    def test_capture_script_names_no_account(self):
        self.assertNotIn("harrison", read(self.script("capture.sh")))


def load_attention(state_dir):
    os.environ["HERDR_PLUGIN_STATE_DIR"] = state_dir
    os.environ["HERDR_SOCKET_PATH"] = "/x/sessions/foo/herdr.sock"
    spec = importlib.util.spec_from_file_location("attention_under_test", ATTENTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AttentionMarkPruning(unittest.TestCase):
    DAY = 24 * 3600

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="herdr-attn-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.mod = load_attention(self.tmp)
        self.applied = []
        self.mod.apply = lambda pane_id, mark: self.applied.append((pane_id, mark))
        self.mod.panes = lambda: [{"pane_id": "w1:p1", "agent_session": {"value": "LIVE"}}]
        self.now = 1_000 * self.DAY
        self.mod.time.time = lambda: self.now
        self.addCleanup(setattr, self.mod.time, "time", __import__("time").time)

    def seed(self, marks):
        self.mod.save_state({"foo": marks})

    def marks(self):
        return self.mod.load_state()["foo"]

    def test_matched_marks_are_applied(self):
        self.seed({"session:LIVE": {"mark": "later", "pane_id": "w1:p1"}})
        self.assertEqual(self.mod.restore_once(), 0)
        self.assertEqual(self.applied, [("w1:p1", "later")])

    def test_old_unmatched_marks_are_pruned_on_the_final_pass(self):
        old = self.now - 30 * self.DAY
        self.seed({"session:GONE": {"mark": "later", "pane_id": "w9:p9", "last_seen": old}})
        self.assertEqual(self.mod.restore_once(prune=True), 0)
        self.assertNotIn("session:GONE", self.marks())

    def test_old_unmatched_marks_survive_an_early_pass(self):
        """Agents report session ids a little after start; never prune before they have."""
        old = self.now - 30 * self.DAY
        self.seed({"session:GONE": {"mark": "later", "pane_id": "w9:p9", "last_seen": old}})
        self.assertEqual(self.mod.restore_once(), 1)
        self.assertIn("session:GONE", self.marks())

    def test_recent_unmatched_marks_survive_the_final_pass(self):
        recent = self.now - 2 * self.DAY
        self.seed({"session:AWAY": {"mark": "review", "pane_id": "w9:p9", "last_seen": recent}})
        self.mod.restore_once(prune=True)
        self.assertIn("session:AWAY", self.marks())

    def test_legacy_marks_without_last_seen_start_the_clock(self):
        self.seed({"session:OLD": {"mark": "later", "pane_id": "w9:p9"}})
        self.mod.restore_once(prune=True)
        self.assertEqual(self.marks()["session:OLD"]["last_seen"], self.now)

    def test_matching_refreshes_last_seen(self):
        old = self.now - 10 * self.DAY
        self.seed({"session:LIVE": {"mark": "later", "pane_id": "w1:p1", "last_seen": old}})
        self.mod.restore_once()
        self.assertEqual(self.marks()["session:LIVE"]["last_seen"], self.now)

    def test_set_mark_records_last_seen(self):
        pane = {"pane_id": "w1:p1", "agent_session": {"value": "LIVE"}}
        self.mod.set_mark(pane, "waiting")
        self.assertEqual(self.marks()["session:LIVE"]["last_seen"], self.now)


if __name__ == "__main__":
    unittest.main()
