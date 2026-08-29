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
