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
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKED = os.path.join(HERE, os.pardir, "shell", "claude_code_toggle.sh")
LIVE = os.path.expanduser("~/claude_code_toggle.sh")

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


class TrackedCopyMatchesLive(unittest.TestCase):

    def test_tracked_copy_is_current(self):
        """The copy is only useful if it is not stale.

        Skipped where the live file does not exist (CI, another machine), so this
        asserts freshness without making the suite machine-dependent.
        """
        if not os.path.isfile(LIVE):
            self.skipTest("no live ~/claude_code_toggle.sh on this machine")
        self.assertEqual(
            read(TRACKED), read(LIVE),
            "tracked copy has drifted from the live file; refresh it with:\n"
            "  cp ~/claude_code_toggle.sh %s" % os.path.normpath(TRACKED),
        )


if __name__ == "__main__":
    unittest.main()
