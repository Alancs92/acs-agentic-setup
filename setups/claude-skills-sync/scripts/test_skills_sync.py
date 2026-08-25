#!/usr/bin/env python3
"""Tests for claude-acs skills.

Run: cd scripts && python3 -m unittest discover -p 'test_*.py'

WHAT THESE TESTS ARE FOR
  The happy path here is trivial -- one `merge --ff-only`. Nearly all the value
  is in the paths where the tool must decline to act, so that is where the tests
  are. Each safety test asserts a *negative*: after the run, the thing that was
  there is still there, byte for byte, and HEAD did not move.

  Every repository used below is created fresh in a TemporaryDirectory. Nothing
  in this file reads, writes, or even resolves the real ~/.claude/skills; the
  target path always comes from the per-test config written into the temp tree.

  The last class is a static audit of the package's own source: no destructive
  git verb, no shell=True, no os.system, and no path from any module other than
  gitops.py to a git subprocess at all.
"""
from __future__ import annotations

import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gitops                    # noqa: E402
import runlog                    # noqa: E402
import schedule as schedule_mod  # noqa: E402
import skills_sync               # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent

# Git children in this file must not see the developer's real git configuration:
# a global `merge.ff = false`, a commit template, or a signing key would make
# these tests pass or fail for reasons that have nothing to do with the code.
_SAVED_ENV = {}
_ISOLATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def setUpModule() -> None:
    for key, value in _ISOLATION.items():
        _SAVED_ENV[key] = os.environ.get(key)
        os.environ[key] = value


def tearDownModule() -> None:
    for key, value in _SAVED_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --- fixture helpers --------------------------------------------------------

def _raw_git(cwd, *args, **kwargs) -> subprocess.CompletedProcess:
    """Unrestricted git, for building fixtures only.

    Production code cannot reach this: it goes through gitops.run_git, whose
    allow-list is asserted by SourceAuditTests below.
    """
    check = kwargs.pop("check", True)
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd), capture_output=True, text=True,
        check=check,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _head(repo: Path) -> str:
    return _raw_git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_count(repo: Path) -> int:
    return int(_raw_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


def _log_subjects(repo: Path) -> List[str]:
    out = _raw_git(repo, "log", "--format=%s").stdout
    return [line for line in out.splitlines() if line]


class Fixture:
    """A throwaway origin + publisher + local clone, wired together.

        origin.git   bare, the "remote"
        publisher/   used to advance the remote; never the subject of a test
        local/       the target repo the tool operates on
        logs/        run log
        config.json  points `target.path` at local/ and nothing else
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.publisher = root / "publisher"
        self.local = root / "local"
        self.log = root / "logs" / "sync.log"
        self.config_path = root / "config.json"

        _raw_git(root, "-c", "init.defaultBranch=main", "init", "--bare",
                 str(self.origin))
        _raw_git(root, "-c", "init.defaultBranch=main", "init",
                 str(self.publisher))
        _write(self.publisher / "SKILL.md", "v1\n")
        _raw_git(self.publisher, "add", "-A")
        _raw_git(self.publisher, "commit", "-m", "initial")
        _raw_git(self.publisher, "remote", "add", "origin", str(self.origin))
        _raw_git(self.publisher, "push", "-u", "origin", "main")
        _raw_git(root, "clone", str(self.origin), str(self.local))

        self.config = {
            "schema_version": 1,
            "target": {
                "path": str(self.local),
                "remote": str(self.origin),
                "remote_name": "origin",
                "branch": "main",
            },
            "policy": {"stale_after_days": 14},
            "log_path": str(self.log),
            "schedule": {
                "interval_days": 1, "weekday": 1, "hour": 7, "minute": 15,
                "label": "com.acs.claude-skills-sync-test",
            },
        }
        self.save_config()

    def save_config(self) -> None:
        _write(self.config_path, json.dumps(self.config, indent=2))

    # -- mutations used to set up each scenario ------------------------------

    def advance_origin(self, subject: str, body: str = "remote\n") -> str:
        _write(self.publisher / "remote-added.md", body)
        _raw_git(self.publisher, "add", "-A")
        _raw_git(self.publisher, "commit", "-m", subject)
        _raw_git(self.publisher, "push", "origin", "main")
        return _head(self.publisher)

    def commit_locally(self, subject: str, body: str = "local\n") -> str:
        _write(self.local / "local-only.md", body)
        _raw_git(self.local, "add", "-A")
        _raw_git(self.local, "commit", "-m", subject)
        return _head(self.local)

    def detach_head(self) -> None:
        """Detach without `checkout` -- that verb is banned even in the tests."""
        (self.local / ".git" / "HEAD").write_text(_head(self.local) + "\n",
                                                  encoding="utf-8")

    def move_to_branch(self, name: str) -> None:
        """Point HEAD at another branch on the same commit, again without
        `checkout`, so the working tree stays clean."""
        _raw_git(self.local, "branch", name)
        (self.local / ".git" / "HEAD").write_text(
            "ref: refs/heads/{0}\n".format(name), encoding="utf-8")

    # -- running the tool ----------------------------------------------------

    def pull(self, *extra) -> int:
        self.save_config()
        return skills_sync.main(["--config", str(self.config_path), "pull"]
                                + list(extra))

    def status(self, *extra) -> int:
        self.save_config()
        return skills_sync.main(["--config", str(self.config_path), "status"]
                                + list(extra))

    def entries(self):
        return runlog.read_entries(self.log)

    def last_outcome(self) -> Optional[str]:
        entries = self.entries()
        return entries[-1].outcome if entries else None


class FixtureCase(unittest.TestCase):
    """Base class giving each test its own temp tree and a silenced stdout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.fx = Fixture(self.root)

        self._devnull = open(os.devnull, "w")
        self.addCleanup(self._devnull.close)
        self._stdout, self._stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = self._devnull

        def _restore():
            sys.stdout, sys.stderr = self._stdout, self._stderr
        self.addCleanup(_restore)


# --- gitops: the chokepoint -------------------------------------------------

class RunGitAllowListTests(unittest.TestCase):

    # Space-joined rather than a list of individual strings so that this test
    # file does not itself contain a string constant equal to a banned verb --
    # which would make SourceAuditTests flag its own auditor.
    DESTRUCTIVE = ("reset clean stash checkout switch restore rebase pull push "
                   "gc filter-branch cherry-pick revert am apply").split()
    DESTRUCTIVE_FLAGS = ("--hard --force --force-with-lease --force-if-includes "
                         "-f -D").split()

    def test_destructive_subcommands_are_unreachable(self):
        for verb in self.DESTRUCTIVE:
            with self.subTest(verb=verb):
                self.assertNotIn(verb, gitops.GIT_SUBCOMMANDS)
                with self.assertRaises(ValueError):
                    gitops.validate_argv([verb])

    def test_allow_list_contains_no_destructive_verb(self):
        overlap = set(gitops.GIT_SUBCOMMANDS) & set(self.DESTRUCTIVE)
        self.assertEqual(set(), overlap)

    def test_only_fetch_and_merge_can_change_anything(self):
        self.assertEqual({"fetch", "merge"}, set(gitops.MUTATING_SUBCOMMANDS))
        self.assertTrue(gitops.MUTATING_SUBCOMMANDS <= set(gitops.GIT_SUBCOMMANDS))

    def test_merge_may_only_be_fast_forward(self):
        self.assertEqual({"--ff-only"}, gitops.GIT_SUBCOMMANDS["merge"])
        for flag in "--force -f --no-ff --squash --strategy=ours".split():
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    gitops.validate_argv(["merge", flag, "refs/remotes/origin/main"])

    def test_unlisted_flags_are_rejected(self):
        with self.assertRaises(ValueError):
            gitops.validate_argv(["rev-parse", "--not-a-real-flag"])

    def test_argv_must_be_a_list_not_a_shell_string(self):
        with self.assertRaises(ValueError):
            gitops.validate_argv("status --porcelain=v1")
        with self.assertRaises(ValueError):
            gitops.validate_argv([])
        with self.assertRaises(ValueError):
            gitops.validate_argv(["status", 3])

    def test_operands_are_allowed_but_flag_shaped_refs_are_not(self):
        gitops.validate_argv(["merge", "--ff-only", "refs/remotes/origin/main"])
        for flag in self.DESTRUCTIVE_FLAGS:
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    gitops.validate_argv(["merge", "--ff-only", flag])
                with self.assertRaises(ValueError):
                    gitops.validate_argv(["fetch", flag, "origin"])

    def test_flag_shaped_remote_and_ref_names_are_refused(self):
        with self.assertRaises(ValueError):
            gitops.fetch(SCRIPTS_DIR, "--upload-pack=touch /tmp/pwned")
        with self.assertRaises(ValueError):
            gitops.merge_ff_only(SCRIPTS_DIR, "--strategy=ours")


class NormalizeRemoteTests(unittest.TestCase):

    EQUIVALENT = [
        "https://github.com/alan-soewargo-hai/acs-claude-skills.git",
        "https://github.com/alan-soewargo-hai/acs-claude-skills",
        "https://github.com/alan-soewargo-hai/acs-claude-skills/",
        "git@github.com:alan-soewargo-hai/acs-claude-skills.git",
        "ssh://git@github.com/alan-soewargo-hai/acs-claude-skills.git",
        "ssh://git@github.com:22/alan-soewargo-hai/acs-claude-skills.git",
        "https://GitHub.com/Alan-Soewargo-HAI/acs-claude-skills.git",
    ]

    def test_all_spellings_of_one_repo_agree(self):
        canonical = gitops.normalize_remote(self.EQUIVALENT[0])
        self.assertEqual("github.com/alan-soewargo-hai/acs-claude-skills", canonical)
        for url in self.EQUIVALENT[1:]:
            with self.subTest(url=url):
                self.assertEqual(canonical, gitops.normalize_remote(url))

    def test_different_repos_do_not_collide(self):
        a = gitops.normalize_remote(self.EQUIVALENT[0])
        for other in ("https://github.com/someone-else/acs-claude-skills.git",
                      "https://github.com/alan-soewargo-hai/other-repo.git",
                      "https://gitlab.com/alan-soewargo-hai/acs-claude-skills"):
            with self.subTest(other=other):
                self.assertNotEqual(a, gitops.normalize_remote(other))

    def test_missing_remote_never_matches_a_configured_one(self):
        self.assertEqual("", gitops.normalize_remote(None))
        self.assertEqual("", gitops.normalize_remote("   "))
        self.assertNotEqual(gitops.normalize_remote(None),
                            gitops.normalize_remote(self.EQUIVALENT[0]))


class StatusParsingTests(unittest.TestCase):

    def test_counts_modified_and_untracked_separately(self):
        payload = " M a.md\0?? new.md\0 M b.md\0?? dir/other.md\0"
        self.assertEqual((2, 2), gitops.parse_status_z(payload))

    def test_rename_record_consumes_its_source_path(self):
        # `R  new\0old\0` is ONE change, not two.
        self.assertEqual((1, 0), gitops.parse_status_z("R  new.md\0old.md\0"))

    def test_filename_containing_a_newline_is_one_entry(self):
        # The reason for -z: a newline-split parser reports 2 here, and an
        # undercount in the other direction is what would let a dirty tree pass
        # the gate.
        self.assertEqual((0, 1), gitops.parse_status_z("?? we\nird.md\0"))

    def test_clean_tree_is_zero(self):
        self.assertEqual((0, 0), gitops.parse_status_z(""))


class RepoStateTests(FixtureCase):

    def test_clean_clone_is_level_with_its_remote(self):
        state = gitops.read_state(self.fx.local, "origin", "main")
        self.assertTrue(state.is_repo)
        self.assertEqual("main", state.branch)
        self.assertFalse(state.dirty)
        self.assertTrue(state.upstream_known)
        self.assertEqual((0, 0), (state.ahead, state.behind))
        self.assertFalse(state.diverged)

    def test_toplevel_of_a_subdirectory_is_the_repo_root(self):
        nested = self.fx.local / "some" / "skill"
        nested.mkdir(parents=True)
        self.assertEqual(self.fx.local.resolve(),
                         Path(gitops.toplevel(nested)).resolve())

    def test_plain_directory_is_not_a_repo(self):
        plain = self.root / "not-a-repo"
        plain.mkdir()
        state = gitops.read_state(plain, "origin", "main")
        self.assertFalse(state.is_repo)

    def test_detached_head_reports_no_branch(self):
        self.fx.detach_head()
        state = gitops.read_state(self.fx.local, "origin", "main")
        self.assertIsNone(state.branch)


# --- pull: the safety properties -------------------------------------------

class PullDirtyTreeTests(FixtureCase):

    def test_modified_file_is_skipped_and_left_byte_identical(self):
        skill = self.fx.local / "SKILL.md"
        precious = "half-written skill I have not committed yet\n"
        skill.write_text(precious, encoding="utf-8")
        self.fx.advance_origin("remote moves on")
        before = _head(self.fx.local)

        code = self.fx.pull()

        self.assertEqual(0, code, "a dirty tree is the normal case, not an error")
        self.assertEqual(runlog.OUTCOME_SKIPPED_DIRTY, self.fx.last_outcome())
        self.assertEqual(precious, skill.read_text(encoding="utf-8"))
        self.assertEqual(before, _head(self.fx.local))

    def test_untracked_file_is_skipped_and_still_there(self):
        stray = self.fx.local / "brand-new-skill" / "SKILL.md"
        _write(stray, "not added to the index yet\n")
        self.fx.advance_origin("remote moves on")
        before = _head(self.fx.local)

        code = self.fx.pull()

        self.assertEqual(0, code)
        self.assertEqual(runlog.OUTCOME_SKIPPED_DIRTY, self.fx.last_outcome())
        self.assertTrue(stray.exists(), "untracked work must survive a pull run")
        self.assertEqual("not added to the index yet\n",
                         stray.read_text(encoding="utf-8"))
        self.assertEqual(before, _head(self.fx.local))

    def test_staged_but_uncommitted_change_is_also_dirt(self):
        _write(self.fx.local / "staged.md", "staged\n")
        _raw_git(self.fx.local, "add", "-A")
        self.fx.advance_origin("remote moves on")
        before = _head(self.fx.local)

        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_SKIPPED_DIRTY, self.fx.last_outcome())
        self.assertEqual(before, _head(self.fx.local))

    def test_dirty_run_still_records_how_far_behind_it_is(self):
        _write(self.fx.local / "SKILL.md", "dirty\n")
        self.fx.advance_origin("remote moves on")
        self.fx.pull()
        self.assertIn("behind=1", self.fx.entries()[-1].detail)


class PullDivergedTests(FixtureCase):

    def test_diverged_history_is_never_reconciled(self):
        self.fx.advance_origin("remote work")
        local_sha = self.fx.commit_locally("local work")
        before_count = _commit_count(self.fx.local)

        code = self.fx.pull()

        self.assertEqual(2, code, "divergence needs a human")
        self.assertEqual(runlog.OUTCOME_SKIPPED_DIVERGED, self.fx.last_outcome())
        # HEAD did not move: no merge, no rebase, no fast-forward.
        self.assertEqual(local_sha, _head(self.fx.local))
        self.assertEqual(before_count, _commit_count(self.fx.local))
        self.assertIn("local work", _log_subjects(self.fx.local))
        # And specifically: no merge commit was fabricated.
        parents = _raw_git(self.fx.local, "log", "-1", "--format=%P").stdout.split()
        self.assertEqual(1, len(parents), "a merge commit would have two parents")

    def test_diverged_run_logs_both_counts(self):
        self.fx.advance_origin("remote work")
        self.fx.commit_locally("local work")
        self.fx.pull()
        detail = self.fx.entries()[-1].detail
        self.assertIn("ahead=1", detail)
        self.assertIn("behind=1", detail)

    def test_local_work_survives_repeated_diverged_runs(self):
        self.fx.advance_origin("remote work")
        sha = self.fx.commit_locally("local work")
        for _ in range(3):
            self.fx.pull()
        self.assertEqual(sha, _head(self.fx.local))
        self.assertTrue((self.fx.local / "local-only.md").exists())


class PullFastForwardTests(FixtureCase):

    def test_clean_and_strictly_behind_fast_forwards(self):
        remote_sha = self.fx.advance_origin("remote work")

        code = self.fx.pull()

        self.assertEqual(0, code)
        self.assertEqual(runlog.OUTCOME_FAST_FORWARDED, self.fx.last_outcome())
        self.assertEqual(remote_sha, _head(self.fx.local))
        self.assertTrue((self.fx.local / "remote-added.md").exists())

    def test_fast_forward_creates_no_merge_commit(self):
        self.fx.advance_origin("remote work")
        self.fx.pull()
        parents = _raw_git(self.fx.local, "log", "-1", "--format=%P").stdout.split()
        self.assertEqual(1, len(parents))

    def test_second_run_is_a_no_op(self):
        self.fx.advance_origin("remote work")
        self.fx.pull()
        sha = _head(self.fx.local)
        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_UP_TO_DATE, self.fx.last_outcome())
        self.assertEqual(sha, _head(self.fx.local))

    def test_unpushed_commits_with_nothing_incoming_are_left_alone(self):
        sha = self.fx.commit_locally("local work")
        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_SKIPPED_AHEAD, self.fx.last_outcome())
        self.assertEqual(sha, _head(self.fx.local))


class PullRefusalTests(FixtureCase):

    def test_wrong_origin_is_refused(self):
        self.fx.config["target"]["remote"] = \
            "https://github.com/someone-else/not-my-skills.git"
        before = _head(self.fx.local)

        code = self.fx.pull()

        self.assertEqual(1, code)
        self.assertEqual(runlog.OUTCOME_REFUSED_REMOTE_MISMATCH,
                         self.fx.last_outcome())
        self.assertEqual(before, _head(self.fx.local))

    def test_ssh_spelling_of_the_same_origin_is_accepted(self):
        # The identity gate must reject a different repo, not a different URL
        # dialect for the same one.
        self.fx.config["target"]["remote"] = str(self.fx.origin) + "/"
        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_UP_TO_DATE, self.fx.last_outcome())

    def test_missing_remote_is_refused(self):
        _raw_git(self.fx.local, "remote", "remove", "origin")
        self.assertEqual(1, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_REFUSED_REMOTE_MISMATCH,
                         self.fx.last_outcome())

    def test_plain_directory_is_refused(self):
        plain = self.root / "not-a-repo"
        plain.mkdir()
        self.fx.config["target"]["path"] = str(plain)
        self.assertEqual(1, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_REFUSED_NOT_A_REPO, self.fx.last_outcome())

    def test_missing_directory_is_refused(self):
        self.fx.config["target"]["path"] = str(self.root / "nope")
        self.assertEqual(1, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_REFUSED_NOT_A_REPO, self.fx.last_outcome())

    def test_directory_inside_another_repo_is_refused(self):
        # ~/.claude/skills being a plain directory while ~/.claude is a repo:
        # rev-parse would answer from the parent, and every later check would
        # describe a repository we were never pointed at.
        inner = self.fx.local / "skills"
        inner.mkdir()
        self.fx.config["target"]["path"] = str(inner)
        self.assertEqual(1, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_REFUSED_NOT_A_REPO, self.fx.last_outcome())
        self.assertIn("toplevel=", self.fx.entries()[-1].detail)

    def test_detached_head_is_skipped(self):
        self.fx.advance_origin("remote work")
        self.fx.detach_head()
        before = _head(self.fx.local)
        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_SKIPPED_DETACHED, self.fx.last_outcome())
        self.assertEqual(before, _head(self.fx.local))

    def test_other_branch_is_skipped(self):
        self.fx.advance_origin("remote work")
        self.fx.move_to_branch("wip")
        before = _head(self.fx.local)
        self.assertEqual(0, self.fx.pull())
        self.assertEqual(runlog.OUTCOME_SKIPPED_BRANCH, self.fx.last_outcome())
        self.assertEqual(before, _head(self.fx.local))

    def test_unknown_upstream_is_skipped(self):
        self.fx.config["target"]["branch"] = "no-such-branch"
        before = _head(self.fx.local)
        self.assertEqual(0, self.fx.pull())
        self.assertIn(self.fx.last_outcome(),
                      (runlog.OUTCOME_SKIPPED_BRANCH,
                       runlog.OUTCOME_SKIPPED_NO_UPSTREAM))
        self.assertEqual(before, _head(self.fx.local))


class PullFetchFailureTests(FixtureCase):

    def test_unreachable_remote_leaves_the_repository_untouched(self):
        # The remote URL still matches config -- it simply is not there any
        # more, which is what an offline laptop looks like from inside git.
        skill = self.fx.local / "SKILL.md"
        before_head = _head(self.fx.local)
        before_text = skill.read_text(encoding="utf-8")
        shutil.rmtree(self.fx.origin)

        code = self.fx.pull()

        self.assertEqual(1, code)
        self.assertEqual(runlog.OUTCOME_FETCH_FAILED, self.fx.last_outcome())
        self.assertEqual(before_head, _head(self.fx.local))
        self.assertEqual(before_text, skill.read_text(encoding="utf-8"))
        self.assertEqual((0, 0), gitops.parse_status_z(
            _raw_git(self.fx.local, "status", "--porcelain=v1",
                     "--untracked-files=all", "-z").stdout))

    def test_fetch_failure_does_not_discard_dirty_work(self):
        skill = self.fx.local / "SKILL.md"
        skill.write_text("uncommitted\n", encoding="utf-8")
        shutil.rmtree(self.fx.origin)
        self.assertEqual(1, self.fx.pull())
        self.assertEqual("uncommitted\n", skill.read_text(encoding="utf-8"))


class RunLogTests(FixtureCase):

    def test_every_run_appends_one_timestamped_line(self):
        self.fx.pull()
        self.fx.advance_origin("remote work")
        self.fx.pull()
        entries = self.fx.entries()
        self.assertEqual(2, len(entries))
        for entry in entries:
            self.assertEqual("pull", entry.command)
            self.assertRegex(entry.timestamp,
                             r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual([runlog.OUTCOME_UP_TO_DATE,
                          runlog.OUTCOME_FAST_FORWARDED],
                         [e.outcome for e in entries])

    def test_every_outcome_constant_is_classified(self):
        outcomes = {value for name, value in vars(runlog).items()
                    if name.startswith("OUTCOME_")}
        self.assertTrue(runlog.SUCCESS_OUTCOMES <= outcomes)
        self.assertTrue(runlog.ATTENTION_OUTCOMES <= outcomes)
        self.assertFalse(runlog.SUCCESS_OUTCOMES & runlog.ATTENTION_OUTCOMES)

    def test_unstructured_noise_in_the_log_is_skipped_not_fatal(self):
        # This file doubles as launchd's StandardOutPath, so a traceback can
        # land between two good lines.
        self.fx.pull()
        with open(self.fx.log, "a", encoding="utf-8") as handle:
            handle.write("Traceback (most recent call last):\n  boom\n")
        self.fx.advance_origin("remote work")
        self.fx.pull()
        self.assertEqual(2, len(self.fx.entries()))

    def test_quiet_run_still_logs(self):
        self.fx.pull("--quiet")
        self.assertEqual(1, len(self.fx.entries()))

    def test_last_success_ignores_skips(self):
        runlog.append(self.fx.log, "2026-08-01T00:00:00Z", "pull",
                      runlog.OUTCOME_FAST_FORWARDED, {})
        runlog.append(self.fx.log, "2026-08-02T00:00:00Z", "pull",
                      runlog.OUTCOME_SKIPPED_DIRTY, {})
        success = runlog.last_success(self.fx.log)
        self.assertIsNotNone(success)
        self.assertEqual("2026-08-01T00:00:00Z", success.timestamp)


# --- status -----------------------------------------------------------------

class StatusTests(FixtureCase):

    def test_clean_synced_repo_is_quiet_and_exits_zero(self):
        self.assertEqual(0, self.fx.status())

    def test_dirty_tree_alone_is_not_an_alarm(self):
        _write(self.fx.local / "SKILL.md", "editing\n")
        _write(self.fx.local / "untracked.md", "new\n")
        self.assertEqual(0, self.fx.status(),
                         "dirty is the normal daily state; alarming on it "
                         "trains the user to ignore the alarm")

    def test_diverged_repo_needs_attention(self):
        self.fx.advance_origin("remote work")
        self.fx.commit_locally("local work")
        self.fx.pull()          # populates remote-tracking refs
        self.assertEqual(2, self.fx.status())

    def test_long_unsynced_repo_needs_attention(self):
        runlog.append(self.fx.log, "2020-01-01T00:00:00Z", "pull",
                      runlog.OUTCOME_UP_TO_DATE, {})
        self.assertEqual(2, self.fx.status())

    def test_recent_sync_is_fine(self):
        runlog.append(self.fx.log, runlog.utcnow_iso(), "pull",
                      runlog.OUTCOME_UP_TO_DATE, {})
        self.assertEqual(0, self.fx.status())

    def test_runs_recorded_but_none_successful_needs_attention(self):
        runlog.append(self.fx.log, runlog.utcnow_iso(), "pull",
                      runlog.OUTCOME_FETCH_FAILED, {})
        self.assertEqual(2, self.fx.status())

    def test_wrong_repo_is_refused_by_status_too(self):
        self.fx.config["target"]["remote"] = "https://example.invalid/other.git"
        self.assertEqual(1, self.fx.status())

    def test_status_does_not_modify_the_repository(self):
        self.fx.advance_origin("remote work")
        before = _head(self.fx.local)
        self.fx.status("--fetch")
        self.assertEqual(before, _head(self.fx.local))

    def test_status_output_is_one_glanceable_block(self):
        buffer = io.StringIO()
        saved = sys.stdout
        sys.stdout = buffer
        try:
            self.fx.status()
        finally:
            sys.stdout = saved
        text = buffer.getvalue()
        for label in ("repo", "branch", "dirty", "unpushed", "behind",
                      "last sync"):
            self.assertIn(label, text)
        self.assertLessEqual(len(text.splitlines()), 8,
                             "status is meant to be read at a glance")


# --- launchd ----------------------------------------------------------------

class LaunchdPlistTests(unittest.TestCase):

    def _config(self, **schedule):
        base = {"interval_days": 1, "weekday": 1, "hour": 7, "minute": 15,
                "label": "com.acs.claude-skills-sync"}
        base.update(schedule)
        return {"schedule": base,
                "log_path": "~/Library/Logs/com.acs.claude-skills-sync.log"}

    def _render(self, **schedule) -> str:
        return schedule_mod.render_launchd_plist(
            self._config(**schedule), SCRIPTS_DIR / "skills_sync.py")

    def test_schedule_is_daily(self):
        xml = self._render()
        self.assertIn("<key>StartCalendarInterval</key>", xml)
        self.assertIn("<key>Hour</key><integer>7</integer>", xml)
        self.assertIn("<key>Minute</key><integer>15</integer>", xml)
        self.assertNotIn("<key>Weekday</key>", xml)
        self.assertNotIn("<key>StartInterval</key>", xml)

    def test_weekly_cadence_still_available_from_config(self):
        xml = self._render(interval_days=7, weekday=3)
        self.assertIn("<key>Weekday</key><integer>3</integer>", xml)

    def test_other_cadence_falls_back_to_start_interval(self):
        xml = self._render(interval_days=3)
        self.assertIn("<key>StartInterval</key>\n\t<integer>259200</integer>", xml)

    def test_job_runs_pull_quietly_and_never_at_load(self):
        xml = self._render()
        self.assertIn("<string>pull</string>", xml)
        self.assertIn("<string>--quiet</string>", xml)
        self.assertIn("<key>RunAtLoad</key>\n\t<false/>", xml)

    def test_log_lives_under_library_logs(self):
        # launchd silently declines to spawn a job whose StandardOutPath
        # directory does not exist. ~/Library/Logs always does; ~/.claude/logs
        # does not.
        xml = self._render()
        expected = str(Path.home() / "Library" / "Logs")
        self.assertIn("<key>StandardOutPath</key>", xml)
        self.assertIn(expected, xml)
        self.assertNotIn("/.claude/logs", xml)

    def test_label_validation(self):
        for bad in ["", "../evil", "a/b", "com.acs sync", "..", ".",
                    "com.acs.\tsync"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    schedule_mod.validate_label(bad)
        self.assertEqual("com.acs.claude-skills-sync",
                         schedule_mod.validate_label("com.acs.claude-skills-sync"))

    def test_out_of_range_schedule_values_are_rejected(self):
        for kwargs in ({"hour": 24}, {"minute": 60}, {"interval_days": 0},
                       {"interval_days": 7, "weekday": 7}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self._render(**kwargs)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("plutil"),
                         "plutil is macOS-only")
    def test_generated_plist_passes_plutil_lint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "com.acs.claude-skills-sync.plist"
            path.write_text(self._render(), encoding="utf-8")
            result = subprocess.run(["plutil", "-lint", str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode,
                             result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("plutil"),
                         "plutil is macOS-only")
    def test_shipped_config_renders_a_valid_daily_plist(self):
        config = json.loads((SCRIPTS_DIR.parent / "config.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(1, config["schedule"]["interval_days"],
                         "this setup is documented as DAILY")
        xml = schedule_mod.render_launchd_plist(
            config, SCRIPTS_DIR / "skills_sync.py")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shipped.plist"
            path.write_text(xml, encoding="utf-8")
            result = subprocess.run(["plutil", "-lint", str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(0, result.returncode,
                             result.stdout + result.stderr)
        self.assertIn("<key>StartCalendarInterval</key>", xml)
        self.assertNotIn("<key>Weekday</key>", xml)


class ShippedConfigTests(unittest.TestCase):

    def setUp(self) -> None:
        self.config = json.loads((SCRIPTS_DIR.parent / "config.json")
                                 .read_text(encoding="utf-8"))

    def test_targets_the_skills_tree_and_says_why_it_is_pull_only(self):
        self.assertEqual("~/.claude/skills", self.config["target"]["path"])
        self.assertEqual("main", self.config["target"]["branch"])
        self.assertTrue(self.config["target"]["remote"])
        self.assertIn("PULL ONLY", self.config["$comment"])

    def test_log_path_is_under_library_logs(self):
        self.assertTrue(self.config["log_path"].startswith("~/Library/Logs/"))

    def test_config_loads_through_the_cli_loader(self):
        loaded = skills_sync.load_config(SCRIPTS_DIR.parent / "config.json")
        self.assertEqual("origin", skills_sync.remote_name(loaded))
        self.assertEqual(14.0, skills_sync.stale_after_days(loaded))

    def test_loader_rejects_a_config_missing_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"schedule": {}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                skills_sync.load_config(path)
            path.write_text(json.dumps({"target": {"path": "~/x"}}),
                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                skills_sync.load_config(path)


# --- static audit of this package's own source ------------------------------

class SourceAuditTests(unittest.TestCase):
    """Grep our own source and prove the destructive verbs are simply absent.

    The check is AST-based rather than textual so that prose in comments and
    docstrings -- which discusses `reset`, `stash` and friends at length,
    because explaining what is forbidden is the point -- cannot trip it. What is
    inspected is the set of string *constants* the code could actually hand to
    subprocess.

    The banned tokens are written as space-joined strings and split at runtime,
    so this file contains no string constant equal to a banned verb and the
    auditor does not flag itself.
    """

    VERBS = ("reset clean stash checkout switch restore rebase push clone "
             "gc filter-branch cherry-pick am apply revert")
    FLAGS = ("--hard --force --force-with-lease --force-if-includes -f -D "
             "--delete --mixed --keep")
    # `push` and `clone` are legitimate in test scaffolding (a fixture has to
    # build a remote); they are banned in production modules only.
    TEST_EXEMPT = "push clone"
    # "pull" is deliberately absent from VERBS: it is this setup's own CLI verb,
    # so the string is everywhere. `git pull` is instead proved unreachable by
    # two other tests -- it is not in gitops.GIT_SUBCOMMANDS
    # (RunGitAllowListTests), and no module except gitops.py may name the git
    # binary at all (test_only_gitops_may_name_the_git_binary below).

    @classmethod
    def setUpClass(cls) -> None:
        cls.all_files = sorted(SCRIPTS_DIR.glob("*.py"))
        cls.production = [p for p in cls.all_files if not p.name.startswith("test_")]
        cls.tests = [p for p in cls.all_files if p.name.startswith("test_")]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _tree(path: Path) -> ast.AST:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    @staticmethod
    def _docstring_ids(tree: ast.AST) -> set:
        ids = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
        return ids

    @classmethod
    def _string_constants(cls, path: Path) -> List[Tuple[int, str]]:
        tree = cls._tree(path)
        skip = cls._docstring_ids(tree)
        out = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip):
                out.append((getattr(node, "lineno", 0), node.value))
        return out

    # -- the audit ----------------------------------------------------------

    def test_the_audit_actually_covers_every_module(self):
        names = {p.name for p in self.production}
        self.assertEqual(
            {"gitops.py", "runlog.py", "schedule.py", "skills_sync.py"}, names,
            "a new module appeared; add it here so it is audited too")
        self.assertTrue(self.tests, "the test module must audit itself as well")

    def test_no_destructive_git_token_in_production_source(self):
        banned = set(self.VERBS.split()) | set(self.FLAGS.split())
        offenders = []
        for path in self.production:
            for lineno, value in self._string_constants(path):
                if value in banned:
                    offenders.append("{0}:{1}: {2!r}".format(
                        path.name, lineno, value))
        self.assertEqual([], offenders,
                         "destructive git token reachable from production code")

    def test_no_destructive_git_token_in_the_tests_either(self):
        banned = ((set(self.VERBS.split()) | set(self.FLAGS.split()))
                  - set(self.TEST_EXEMPT.split()))
        offenders = []
        for path in self.tests:
            for lineno, value in self._string_constants(path):
                if value in banned:
                    offenders.append("{0}:{1}: {2!r}".format(
                        path.name, lineno, value))
        self.assertEqual([], offenders)

    def test_only_gitops_may_name_the_git_binary(self):
        offenders = []
        for path in self.production:
            if path.name == "gitops.py":
                continue
            for lineno, value in self._string_constants(path):
                if value == "git":
                    offenders.append("{0}:{1}".format(path.name, lineno))
        self.assertEqual([], offenders,
                         "every git call must go through gitops.run_git")

    def test_no_shell_execution_anywhere(self):
        offenders = []
        for path in self.all_files:
            tree = self._tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        offenders.append("{0}:{1}: shell=".format(
                            path.name, node.lineno))
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else getattr(func, "id", ""))
                module = (getattr(func.value, "id", "")
                          if isinstance(func, ast.Attribute) else "")
                if module == "os" and name in ("system", "popen", "spawnl",
                                               "execv", "execvp"):
                    offenders.append("{0}:{1}: os.{2}".format(
                        path.name, node.lineno, name))
                if module == "subprocess" and name in ("getoutput",
                                                       "getstatusoutput"):
                    offenders.append("{0}:{1}: subprocess.{2}".format(
                        path.name, node.lineno, name))
        self.assertEqual([], offenders)

    def test_every_subprocess_call_uses_an_argv_list(self):
        offenders = []
        for path in self.all_files:
            tree = self._tree(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute)
                        and getattr(func.value, "id", "") == "subprocess"):
                    continue
                if not node.args:
                    offenders.append("{0}:{1}: no argv".format(
                        path.name, node.lineno))
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    offenders.append("{0}:{1}: string command line".format(
                        path.name, node.lineno))
        self.assertEqual([], offenders)

    def test_every_git_invocation_names_an_explicit_cwd(self):
        tree = self._tree(SCRIPTS_DIR / "gitops.py")
        seen = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and getattr(node.func.value, "id", "") == "subprocess"):
                seen += 1
                self.assertIn("cwd", [k.arg for k in node.keywords],
                              "git must never run in the process's own cwd")
        self.assertEqual(1, seen,
                         "gitops.py should contain exactly one subprocess call")


if __name__ == "__main__":
    unittest.main()
