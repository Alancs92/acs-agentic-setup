#!/usr/bin/env python3
"""The only place in claude-skills-sync that is allowed to invoke git.

  run_git          the single chokepoint; refuses anything not on the allow-list
  normalize_remote compare two remote URLs written in different dialects
  read_state       one RepoState describing the target repo right now
  fetch            update remote-tracking refs (the only network call)
  merge_ff_only    the ONLY mutating operation this package can perform

WHY AN ALLOW-LIST AND NOT A DENY-LIST
  This package runs unattended, on a schedule, against `~/.claude/skills` --
  which is not a checkout of published work but the user's live, constantly-
  edited, frequently-uncommitted working tree. It is the only copy of whatever
  they were mid-way through writing.

  A deny-list ("never call reset --hard") fails open: the next person adds
  `git checkout -- .` or `git restore`, the deny-list does not mention it, and a
  night's work is gone with no error message. So run_git fails CLOSED instead.
  Every subcommand must be named in GIT_SUBCOMMANDS and every flag must be named
  in that subcommand's set. A verb nobody has thought about is refused by
  default, which is the correct answer for a job with these stakes.

  Consequence worth stating plainly: `reset`, `clean`, `stash`, `checkout`,
  `switch`, `restore`, `rebase`, `pull` and every `--force` variant are not
  reachable from this code. There is no flag, config key or environment variable
  that enables them. Adding one means editing the table below, which is exactly
  the amount of friction that decision deserves.

Inputs   repo: a path; argv: a list of str (never a shell string)
Outputs  GitResult(returncode, stdout, stderr); GitError on failure when check
Raises   ValueError for a policy violation -- always a programming error, never
         something a config file or a repo state can trigger
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

GIT = "git"

#: Seconds before a git invocation is abandoned. A scheduled job must never be
#: able to wedge forever on a credential prompt or a hung TLS handshake.
DEFAULT_TIMEOUT = 120

#: subcommand -> the option flags it may be passed.
#:
#: Operands (anything not starting with "-") are allowed freely: a bare operand
#: is a ref or a path, and no git subcommand on this list can destroy work
#: without a flag. Flags are what turn a query into an act, so flags are
#: enumerated. Note what is absent -- there is no entry for reset, clean, stash,
#: checkout, switch, restore, rebase, pull, push or gc, so calling one raises.
GIT_SUBCOMMANDS: Dict[str, Set[str]] = {
    "rev-parse": {"--show-toplevel", "--abbrev-ref", "--verify", "--quiet",
                  "--is-inside-work-tree"},
    "symbolic-ref": {"--short", "--quiet"},
    "remote": set(),
    "status": {"--porcelain=v1", "--untracked-files=all", "-z"},
    "rev-list": {"--count", "--left-right"},
    "log": {"-1", "--format=%H"},
    "fetch": set(),
    "merge": {"--ff-only"},
}

#: The subset of the above that can change anything on disk. `fetch` writes only
#: remote-tracking refs and objects, never the working tree or HEAD; `merge
#: --ff-only` advances HEAD but by construction cannot rewrite or discard a
#: commit. Everything else is a pure query. Asserted by the test suite so that
#: adding a mutating verb without noticing is not possible.
MUTATING_SUBCOMMANDS = frozenset({"fetch", "merge"})


class GitResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        return self.stdout.strip()


class GitError(RuntimeError):
    """A git command that was allowed to run, and failed."""

    def __init__(self, argv: Sequence[str], result: GitResult) -> None:
        self.argv = list(argv)
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        super().__init__(f"git {' '.join(argv)} exited {result.returncode}: {detail}")


# --- the chokepoint ---------------------------------------------------------

def validate_argv(argv: Sequence[str]) -> None:
    """Raise ValueError unless `argv` is a read-only-or-fast-forward git call.

    Deliberately strict about *shape* as well as content: a str argv would be
    split by subprocess into single characters on some platforms and is always a
    sign the caller meant to build a shell string.
    """
    if isinstance(argv, (str, bytes)):
        raise ValueError(
            "git arguments must be a list of strings, not a single string; a "
            "string is one step away from a shell command line"
        )
    items = list(argv)
    if not items:
        raise ValueError("empty git argument list")
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"non-string git argument: {item!r}")
        if "\0" in item:
            raise ValueError("NUL byte in git argument")

    subcommand = items[0]
    if subcommand not in GIT_SUBCOMMANDS:
        raise ValueError(
            f"git subcommand {subcommand!r} is not on the allow-list. This "
            "package may only read the repository or fast-forward it; see the "
            "module docstring for why the list fails closed."
        )

    allowed_flags = GIT_SUBCOMMANDS[subcommand]
    for item in items[1:]:
        if item.startswith("-") and item not in allowed_flags:
            raise ValueError(
                f"flag {item!r} is not allowed for `git {subcommand}`; allowed: "
                f"{sorted(allowed_flags) or 'none'}"
            )


def _child_env() -> Dict[str, str]:
    """Environment for git children.

    GIT_TERMINAL_PROMPT=0 is the important one: under launchd there is no
    terminal, so a credential prompt would block until the timeout rather than
    failing, and the job would look hung rather than broken.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.setdefault("LC_ALL", "C")
    return env


def run_git(repo, argv: Sequence[str], check: bool = True,
            timeout: int = DEFAULT_TIMEOUT) -> GitResult:
    """Run one allow-listed git command inside `repo`.

    Always an argv list, always an explicit cwd, never shell=True. `check=False`
    returns the non-zero result instead of raising, for the cases where "this
    ref does not exist" is an answer rather than an error.
    """
    validate_argv(argv)
    items = list(argv)
    completed = subprocess.run(
        [GIT] + items,
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_child_env(),
    )
    result = GitResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        raise GitError(items, result)
    return result


# --- remote identity --------------------------------------------------------

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_USERINFO = re.compile(r"^[^/@]+@")
_DOT_GIT = re.compile(r"\.git/*$")


def normalize_remote(url: Optional[str]) -> str:
    """Reduce a git remote URL to a comparable `host/path` form.

    The same repository is spelled at least four ways -- https, ssh://, the
    scp-like `git@host:owner/repo`, with and without `.git` -- and the identity
    check in pull() must not refuse to run merely because the user re-cloned
    over SSH. Comparison is case-folded, which is correct for GitHub/GitLab/
    Bitbucket and harmless for the local paths used in tests.

    Returns "" for None/blank so a missing remote never compares equal to a
    configured one.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    stripped = _SCHEME.sub("", raw)
    stripped = _USERINFO.sub("", stripped)

    head, slash, tail = stripped.partition("/")
    if ":" in head:
        host, _, rest = head.partition(":")
        if rest.isdigit():
            head = host                      # ssh://host:22/owner/repo
        else:
            head = host                      # scp-like host:owner/repo
            tail = rest + ("/" + tail if slash else "")
        stripped = head + "/" + tail
    stripped = _DOT_GIT.sub("", stripped)
    return stripped.rstrip("/").lower()


# --- repository state -------------------------------------------------------

class RepoState(NamedTuple):
    """Everything pull() and status() need, gathered in one pass.

    `branch` is None for a detached HEAD. `upstream_known` is False when the
    remote-tracking ref does not exist yet, in which case ahead/behind are 0 and
    mean nothing.
    """

    path: Path
    is_repo: bool
    toplevel: Optional[Path]
    remote_url: Optional[str]
    branch: Optional[str]
    head: Optional[str]
    modified: int
    untracked: int
    ahead: int
    behind: int
    upstream_known: bool

    @property
    def dirty(self) -> bool:
        return (self.modified + self.untracked) > 0

    @property
    def diverged(self) -> bool:
        return self.upstream_known and self.ahead > 0 and self.behind > 0


def _not_a_repo(path: Path) -> RepoState:
    return RepoState(path=path, is_repo=False, toplevel=None, remote_url=None,
                     branch=None, head=None, modified=0, untracked=0,
                     ahead=0, behind=0, upstream_known=False)


def parse_status_z(payload: str) -> Tuple[int, int]:
    """(modified, untracked) from `status --porcelain=v1 --untracked-files=all -z`.

    NUL-delimited rather than newline-delimited because a filename may legally
    contain a newline, and undercounting dirt is the one direction this code
    must never fail in -- an undercount is what turns "skip, the tree is dirty"
    into "go ahead and merge".

    Rename and copy records carry a second NUL-separated field (the source
    path); it is consumed rather than counted as its own entry.
    """
    modified = 0
    untracked = 0
    records = payload.split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        code = record[:2]
        if code == "??":
            untracked += 1
            continue
        modified += 1
        if code[:1] in ("R", "C"):
            i += 1
    return modified, untracked


def toplevel(repo) -> Optional[Path]:
    """The root of the work tree containing `repo`, or None if there is none.

    Used to reject the case where the *target* is not a repo but something above
    it is -- `~/.claude` being a repo while `~/.claude/skills` is a plain
    directory would otherwise make every check pass against the wrong tree.
    """
    path = Path(repo)
    if not path.is_dir():
        return None
    result = run_git(path, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0 or not result.out:
        return None
    return Path(result.out)


def remote_url(repo, remote_name: str) -> Optional[str]:
    if remote_name.startswith("-"):
        raise ValueError(f"remote name may not look like a flag: {remote_name!r}")
    result = run_git(repo, ["remote", "get-url", remote_name], check=False)
    if result.returncode != 0:
        return None
    return result.out or None


def current_branch(repo) -> Optional[str]:
    """Short branch name, or None when HEAD is detached.

    symbolic-ref rather than `rev-parse --abbrev-ref HEAD`, because the latter
    reports the literal string "HEAD" for a detached head, which is
    indistinguishable from a branch actually named HEAD.
    """
    result = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"],
                     check=False)
    if result.returncode != 0 or not result.out:
        return None
    return result.out


def head_commit(repo) -> Optional[str]:
    result = run_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"],
                     check=False)
    return result.out or None


def ref_exists(repo, ref: str) -> bool:
    result = run_git(repo, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                     check=False)
    return result.returncode == 0 and bool(result.out)


def ahead_behind(repo, ref: str) -> Optional[Tuple[int, int]]:
    """(commits local has that `ref` lacks, commits `ref` has that local lacks).

    None when `ref` does not resolve, which is a normal state on a repo whose
    remote-tracking refs have never been fetched.
    """
    if not ref_exists(repo, ref):
        return None
    result = run_git(repo, ["rev-list", "--count", "--left-right",
                            "HEAD..." + ref], check=False)
    if result.returncode != 0:
        return None
    parts = result.out.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:  # pragma: no cover - git does not emit non-integers here
        return None


def tracking_ref(remote_name: str, branch: str) -> str:
    return "refs/remotes/{0}/{1}".format(remote_name, branch)


def read_state(repo, remote_name: str, branch: str) -> RepoState:
    """One snapshot of the target repository. Never raises for a missing or
    non-git path -- `is_repo=False` is the answer, and the caller decides."""
    path = Path(repo)
    if not path.is_dir():
        return _not_a_repo(path)

    root = toplevel(path)
    if root is None:
        return _not_a_repo(path)

    status = run_git(path, ["status", "--porcelain=v1", "--untracked-files=all",
                            "-z"], check=False)
    modified, untracked = parse_status_z(status.stdout if status.returncode == 0
                                         else "")

    counts = ahead_behind(path, tracking_ref(remote_name, branch))
    return RepoState(
        path=path,
        is_repo=True,
        toplevel=root,
        remote_url=remote_url(path, remote_name),
        branch=current_branch(path),
        head=head_commit(path),
        modified=modified,
        untracked=untracked,
        ahead=counts[0] if counts else 0,
        behind=counts[1] if counts else 0,
        upstream_known=counts is not None,
    )


# --- the two things that touch anything -------------------------------------

def fetch(repo, remote_name: str, timeout: int = DEFAULT_TIMEOUT) -> GitResult:
    """Update remote-tracking refs. Touches no working-tree file and not HEAD.

    Returned rather than raised on failure: an offline laptop is the normal case
    for a scheduled job, and the caller logs it and exits rather than crashing.
    """
    if remote_name.startswith("-"):
        raise ValueError(f"remote name may not look like a flag: {remote_name!r}")
    return run_git(repo, ["fetch", remote_name], check=False, timeout=timeout)


def merge_ff_only(repo, ref: str) -> GitResult:
    """Advance HEAD to `ref`, or fail.

    --ff-only is load-bearing rather than stylistic: it makes the operation
    incapable of creating a merge commit, incapable of rewriting history, and
    incapable of succeeding at all unless the current HEAD is already an
    ancestor of `ref`. If anything has changed underneath us since read_state()
    -- a commit landed a millisecond ago, say -- git refuses and we log it,
    rather than reconciling the difference on the user's behalf.
    """
    if ref.startswith("-"):
        raise ValueError(f"ref may not look like a flag: {ref!r}")
    return run_git(repo, ["merge", "--ff-only", ref], check=False)
