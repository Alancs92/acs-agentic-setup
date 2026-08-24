#!/usr/bin/env python3
"""Enumerate the units claude-skills-backup will snapshot, and their files.

This is the first stage of the pipeline. It turns the six heterogeneous entries
under `config['sources']` into one uniform, sorted list of `Unit`s, so every
stage downstream (hashing, store, retention) sees a single shape and needs no
per-source special cases.

Two behaviours here are load-bearing:

  Symlinks resolve to content. ~13 of the 85 skills in `~/.claude/skills` are
  symlinks into `~/repos/claude-bootstrap/.claude/skills/`. A backup that stored
  them as links would restore 13 dangling pointers into a repo that may not
  exist on the recovering machine, so `source_path` is always the resolved real
  path and the files walked are real bytes.

  A missing or unreadable source is never fatal. An unplugged external volume,
  a chmod accident or a not-yet-created `~/.claude/agents` must cost you that
  one source, not the whole run. Problems are collected and returned alongside
  the units (see `discover_with_problems`) rather than raised.

Inputs:  the parsed config.json dict -- `config['sources']` and, for
         `iter_files`, `config['exclude_globs']`.
Outputs: `discover(config)` -> list[Unit] sorted by (kind, name)
         `iter_files(unit, exclude_globs)` -> list[Path] sorted, exclusions
         applied; a single-element list for `is_file` units.

Nothing here writes to disk or reads the clock.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern, Tuple

# The shared types module is `backup_types`, not `types`: a `types.py` in this
# directory shadows the stdlib module and kills the interpreter at startup.
from backup_types import (
    KIND_AGENT,
    KIND_CLAUDE_MD,
    KIND_COMMAND,
    KIND_HOOK,
    KIND_PLUGIN_MANIFEST,
    KIND_SETTINGS,
    KIND_SKILL,
    Unit,
    expand,
)

# Sources whose top-level children each become one unit.
_DIR_SOURCES = (
    ("skills", KIND_SKILL),
    ("commands", KIND_COMMAND),
    ("agents", KIND_AGENT),
    ("hooks", KIND_HOOK),
)

# Emitted per account, in this order, when the file exists.
_SETTINGS_FILES = ("settings.json", "settings.local.json")
_CLAUDE_MD = "CLAUDE.md"

# `~/.claude/plugins` is 509M, of which cache/ is 433M and marketplaces/ 75M --
# both refetchable. Manifests mode only ever touches top-level *.json, so those
# two directories are never even listed, let alone walked.
_PLUGIN_MODE_DEFAULT = "manifests"


# --- public API -------------------------------------------------------------
def discover(config: dict) -> List[Unit]:
    """Enumerate every enabled unit from config['sources'].

    Symlinks are resolved to their real target; a symlinked skill is backed up by
    content, not as a link. Units are returned sorted by (kind, name).
    Unreadable sources are skipped, never fatal.
    """
    return discover_with_problems(config)[0]


def discover_with_problems(config: dict) -> Tuple[List[Unit], List[str]]:
    """`discover`, plus the human-readable reasons any source was skipped.

    Additive to the frozen contract: `discover` is the contracted entry point and
    delegates here. The problem strings exist so a run can *report* "agents/ was
    unreadable" instead of silently backing up less than you think it did.
    """
    units: List[Unit] = []
    problems: List[str] = []
    sources = config.get("sources") or {}

    for key, kind in _DIR_SOURCES:
        _collect_dir_source(sources.get(key), key, kind, units, problems)
    _collect_settings(sources.get("settings"), units, problems)
    _collect_plugins(sources.get("plugins"), units, problems)

    units.sort(key=lambda unit: (unit.kind, unit.name))
    return units, problems


def iter_files(unit: Unit, exclude_globs: List[str]) -> List[Path]:
    """Every file belonging to a unit, sorted, exclusions applied.

    For is_file units this is a single-element list.

    Paths inside a directory unit are returned *unresolved* relative to the unit
    root: resolving a nested symlink would move the file outside the unit and
    destroy the relative path the content hash is built from. Reading the
    returned path still follows the link, so the bytes are the real ones.
    """
    if unit.is_file:
        return [unit.source_path] if unit.source_path.is_file() else []

    root = unit.source_path
    if not root.is_dir():
        return []

    matcher = _Matcher(exclude_globs)
    found: List[Path] = []
    seen_dirs = set()

    # followlinks=True is required (a symlinked skill's subdirectories may
    # themselves be links); seen_dirs is what stops that from looping forever.
    # os.walk's default onerror swallows permission errors, which is exactly the
    # "skip and continue" behaviour we want for an unreadable subdirectory.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real_dir = os.path.realpath(dirpath)
        if real_dir in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(real_dir)

        rel_dir = _relative_posix(dirpath, root)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not matcher.excludes_dir(_join_rel(rel_dir, name))
        )
        for name in sorted(filenames):
            if matcher.excludes(_join_rel(rel_dir, name)):
                continue
            candidate = Path(dirpath) / name
            if candidate.is_file():  # drops broken links, sockets, fifos
                found.append(candidate)

    return sorted(found)


# --- source collectors ------------------------------------------------------
def _collect_dir_source(
    spec: Optional[dict],
    key: str,
    kind: str,
    units: List[Unit],
    problems: List[str],
) -> None:
    """One unit per non-hidden top-level child of `spec['path']`."""
    if not _enabled(spec):
        return
    root = _usable_dir(spec.get("path"), key, problems)
    if root is None:
        return

    for child in _children(root, key, problems):
        if child.name.startswith("."):
            continue  # .DS_Store, .gitignore, the skills repo's own .git
        if not child.exists():
            problems.append("%s: broken symlink skipped: %s" % (key, child))
            continue
        real = expand(child)
        units.append(Unit(kind, child.name, real, real.is_file()))


def _collect_settings(
    spec: Optional[dict], units: List[Unit], problems: List[str]
) -> None:
    """settings.json / settings.local.json / CLAUDE.md, per account."""
    if not _enabled(spec):
        return
    root = _usable_dir(spec.get("path"), "settings", problems)
    if root is None:
        return

    for account in spec.get("accounts") or []:
        account_dir = root / account
        if not account_dir.is_dir():
            problems.append("settings: account directory missing: %s" % account_dir)
            continue
        wanted = [(name, KIND_SETTINGS) for name in _SETTINGS_FILES]
        wanted.append((_CLAUDE_MD, KIND_CLAUDE_MD))
        for filename, kind in wanted:
            path = account_dir / filename
            if path.is_file():
                units.append(
                    Unit(kind, "%s/%s" % (account, filename), expand(path), True)
                )


def _collect_plugins(
    spec: Optional[dict], units: List[Unit], problems: List[str]
) -> None:
    """Manifests only by default; the whole tree under mode 'full'."""
    if not _enabled(spec):
        return
    root = _usable_dir(spec.get("path"), "plugins", problems)
    if root is None:
        return

    if (spec.get("mode") or _PLUGIN_MODE_DEFAULT) == "full":
        units.append(Unit(KIND_PLUGIN_MANIFEST, root.name, root, False))
        return

    for child in _children(root, "plugins", problems):
        if child.name.startswith(".") or child.suffix != ".json":
            continue
        if not child.is_file():
            continue
        units.append(
            Unit(KIND_PLUGIN_MANIFEST, child.stem, expand(child), True)
        )


# --- filesystem helpers -----------------------------------------------------
def _enabled(spec: Optional[dict]) -> bool:
    return bool(spec) and bool(spec.get("enabled"))


def _usable_dir(
    path_like, label: str, problems: List[str]
) -> Optional[Path]:
    """Expand `path_like` and return it only if it is a readable directory."""
    if not path_like:
        problems.append("%s: no path configured" % label)
        return None
    path = expand(path_like)
    if not path.exists():
        problems.append("%s: source missing: %s" % (label, path))
        return None
    if not path.is_dir():
        problems.append("%s: source is not a directory: %s" % (label, path))
        return None
    if not os.access(str(path), os.R_OK | os.X_OK):
        problems.append("%s: source unreadable: %s" % (label, path))
        return None
    return path


def _children(root: Path, label: str, problems: List[str]) -> List[Path]:
    """Sorted direct children of `root`; an unreadable dir yields nothing."""
    try:
        return sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        problems.append("%s: cannot list %s: %s" % (label, root, exc))
        return []


def _relative_posix(dirpath: str, root: Path) -> str:
    """POSIX-style path of `dirpath` relative to `root`; '' for the root."""
    rel = os.path.relpath(dirpath, str(root))
    return "" if rel == "." else rel.replace(os.sep, "/")


def _join_rel(rel_dir: str, name: str) -> str:
    return "%s/%s" % (rel_dir, name) if rel_dir else name


# --- exclusion globs --------------------------------------------------------
class _Matcher:
    """Gitignore-flavoured glob matching against unit-relative POSIX paths.

    `fnmatch` and `PurePath.match` both mishandle the patterns this project ships
    with: `fnmatch`'s `*` happily crosses `/`, and on Python < 3.13
    `PurePath.match('**/.git/**')` does not match `.git/config` at all -- which
    would quietly let an entire `.git` directory into the backup blobs. So the
    patterns are compiled here with explicit semantics:

      `**/`  zero or more leading directories       `*`  anything but '/'
      `/**`  everything underneath                  `?`  one char, not '/'
      a pattern containing no '/' matches at any depth, as git does.

    Matching is against the unit-relative path only, never the absolute path: a
    pattern like `**/cache/**` must not nuke a whole unit merely because the
    unit happens to live under some `.../cache/...` directory.
    """

    __slots__ = ("_patterns",)

    _cache: Dict[str, Pattern] = {}

    def __init__(self, globs: Optional[Iterable[str]]):
        self._patterns = [self._compile(g) for g in (globs or []) if g]

    def excludes(self, relpath: str) -> bool:
        """True when the file at `relpath` must be left out."""
        return any(p.match(relpath) for p in self._patterns)

    def excludes_dir(self, relpath: str) -> bool:
        """True when the directory at `relpath` must not be descended into.

        The trailing slash is what makes `**/.git/**` prune `.git` itself: the
        compiled `/.*` tail matches the empty remainder.
        """
        return self.excludes(relpath) or self.excludes(relpath + "/")

    @classmethod
    def _compile(cls, glob: str) -> Pattern:
        compiled = cls._cache.get(glob)
        if compiled is None:
            body = _translate(glob)
            if "/" not in glob:
                body = "(?:.*/)?" + body  # bare names match at any depth
            compiled = re.compile(body + r"\Z")
            cls._cache[glob] = compiled
        return compiled


#: Public alias. `_Matcher` is the SINGLE glob engine for `exclude_globs` across
#: this setup -- `sync.py` imports it so the live mirror and the blob store make
#: identical exclusion decisions. Do not reach for `fnmatch` or `PurePath.match`
#: as a substitute: both fail open on `**/.git/**` (see contracts.md), which
#: would put .git trees in OneDrive. Exported so that coupling is declared
#: rather than reaching through a private name.
Matcher = _Matcher


def _translate(glob: str) -> str:
    """Compile one glob to a regex body with '**'-aware, '/'-respecting rules."""
    out: List[str] = []
    i, n = 0, len(glob)
    while i < n:
        char = glob[i]
        if char == "*":
            j = i
            while j < n and glob[j] == "*":
                j += 1
            if j - i >= 2:  # '**'
                if glob[j : j + 1] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                else:
                    out.append(".*")
                    i = j
            else:
                out.append("[^/]*")
                i = j
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            closing = _class_end(glob, i)
            if closing is None:
                out.append(re.escape(char))
                i += 1
            else:
                inner = glob[i + 1 : closing].replace("\\", "\\\\")
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append("[%s]" % inner)
                i = closing + 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


def _class_end(glob: str, start: int) -> Optional[int]:
    """Index of the ']' closing the character class opened at `start`."""
    i = start + 1
    if i < len(glob) and glob[i] in "!^":
        i += 1
    if i < len(glob) and glob[i] == "]":
        i += 1
    while i < len(glob) and glob[i] != "]":
        i += 1
    return i if i < len(glob) else None
