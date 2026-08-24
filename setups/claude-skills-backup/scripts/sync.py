#!/usr/bin/env python3
"""OneDrive placement and scheduling for claude-skills-backup.

This is the module that touches the user's real corporate OneDrive, so every
function here is written defensively.

  resolve_storage       where this run's blobs and index.db should live
  refresh_live_mirror   the browsable flat "Claude User Skills" replica
  place_index           put index.db beside objects/ without ever tearing it
  render_launchd_plist  the launchd job that runs the backup on a schedule

Inputs
  config              the parsed config.json dict (storage + schedule blocks)
  units               list[Unit] from discovery.discover(); only KIND_SKILL
                      units are mirrored
  exclude_globs       config['exclude_globs']
  secrets_config      config['secrets'], handed straight to redact.redact_bytes
  db_path/blob_root   filesystem paths, already expand()-ed by the caller

Outputs
  (Path, bool) for resolve_storage, {'added','updated','removed'} for
  refresh_live_mirror, None for place_index, plist XML text for
  render_launchd_plist.

WHY DEGRADED MODE EXISTS
  OneDrive is a network-backed mount. It is unmounted while the laptop is on a
  plane, mid-relogin, or when the corporate token expires -- states that are
  entirely normal and entirely outside our control. A backup that refuses to run
  because the cloud is offline is a backup that is missing on exactly the days
  something goes wrong. So an absent or unwritable OneDrive is never fatal: the
  run writes to the local fallback, is recorded as `degraded`, and the next run
  with the cloud back reconciles it.

WHY THE DELETION GUARD EXISTS
  refresh_live_mirror() deletes directories to make the mirror match the current
  set of skills. Those deletes land inside the user's real OneDrive, which is
  replicated to corporate cloud storage and to every other machine on the
  account. A wrong root (relative path, unresolved symlink, "/") or a symlink
  planted inside the mirror would turn a housekeeping pass into data loss that
  syncs everywhere before anyone notices. Every unlink in this module therefore
  goes through _assert_inside_mirror(), the mirror root must be absolute and
  already resolved, and nothing is ever followed through a symlink.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape as _xml_escape

from backup_types import KIND_SKILL, Unit, expand

# One glob engine for the whole pipeline. discovery.py deliberately does not use
# fnmatch (its '*' crosses '/') or PurePath.match (which misses '**/.git/**' on
# Python < 3.13), and carries a gitignore-flavoured compiler instead. Reusing it
# is what keeps `exclude_globs` meaning the same thing in the blobs and in the
# mirror -- a second engine would eventually exclude a file from one and copy it
# into the other. Prefers a public alias if discovery ever exports one.
try:  # pragma: no cover - exercised by whichever name exists
    from discovery import Matcher as _ExcludeMatcher
except ImportError:  # pragma: no cover
    from discovery import _Matcher as _ExcludeMatcher

# Payload of one mirrored file: the bytes to write and whether it is executable.
_Payload = Dict[str, Tuple[bytes, bool]]

MIRROR_ROOT_MIN_PARTS = 3   # refuse "/", "/Users" and friends outright
INDEX_NAME = "index.db"

# Matches com.acs.tide-sync.plist. launchd jobs inherit almost nothing, so the
# PATH has to be spelled out or the script cannot find its interpreter's tools.
# The first entry is ~/.local/bin, filled in against the real home at render
# time.
_LAUNCHD_PATH_TAIL = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


# --- storage resolution -----------------------------------------------------

def _is_writable_dir(path: Path) -> bool:
    """True only if we can actually create a file in `path`.

    os.access() is not enough: it consults the permission bits, while a
    OneDrive mount can be present and readable yet reject writes (expired token,
    full quota, read-only placeholder state). The only honest test is to write.
    """
    if not path.is_dir():
        return False
    try:
        fd, probe = tempfile.mkstemp(prefix=".acs-backup-writetest-",
                                     dir=str(path))
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def resolve_storage(config: dict) -> Tuple[Path, bool]:
    """Return (active blob root, is_degraded).

    Prefers storage.onedrive_root. Falls back to storage.local_fallback when the
    OneDrive path is absent or not writable -- degraded, never fatal.
    """
    storage = config.get("storage", {})
    configured = storage.get("onedrive_root")

    if configured:
        onedrive = expand(configured)
        if onedrive.is_dir():
            if _is_writable_dir(onedrive):
                return onedrive, False
        elif not onedrive.exists():
            # First run with the cloud already mounted: our subfolder simply
            # does not exist yet, so create it. Only the *immediate* parent is
            # considered -- if that is missing the mount itself is gone, and
            # materialising the whole CloudStorage tree as ordinary local
            # directories would quietly hide the outage and confuse OneDrive
            # when it comes back.
            parent = onedrive.parent
            if _is_writable_dir(parent):
                try:
                    onedrive.mkdir(parents=False, exist_ok=True)
                    return onedrive, False
                except OSError:
                    pass
        # onedrive.exists() but is not a directory -> fall through, degraded.

    fallback = expand(storage["local_fallback"])
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, True


# --- mirror safety ----------------------------------------------------------

def _validated_mirror_root(mirror_root) -> Path:
    """Refuse to operate unless the mirror root is safe to delete inside."""
    root = Path(mirror_root)
    if not root.is_absolute():
        raise ValueError(
            f"mirror root must be an absolute path, got {root!r}; "
            "call backup_types.expand() before crossing this boundary"
        )
    resolved = root.resolve()
    if resolved != root:
        raise ValueError(
            f"mirror root must already be resolved: {root!r} resolves to "
            f"{resolved!r}. An unresolved symlink component would make every "
            "'is this inside the mirror' check compare against the wrong tree"
        )
    if len(root.parts) < MIRROR_ROOT_MIN_PARTS or root == Path.home():
        raise ValueError(
            f"refusing to use {root!r} as a mirror root: too close to the "
            "filesystem root for a directory this code deletes inside"
        )
    return root


def _assert_inside_mirror(root: Path, target: Path) -> None:
    """Raise unless `target` is a strict descendant of `root`.

    The target's *parent* is resolved, never the target itself -- resolving the
    target would follow a symlink and cheerfully report the outside directory it
    points at as the thing we are about to delete.
    """
    root = Path(root)
    target = Path(target)
    if not root.is_absolute() or not target.is_absolute():
        raise ValueError(f"non-absolute path in delete guard: {target!r}")
    try:
        parent = target.parent.resolve()
    except OSError as exc:  # pragma: no cover - resolve is non-strict
        raise ValueError(f"cannot resolve parent of {target!r}: {exc}") from exc
    candidate = parent / target.name
    if candidate == root:
        raise ValueError(f"refusing to operate on the mirror root itself: {root!r}")
    if root not in candidate.parents:
        raise ValueError(
            f"refusing to touch {target!r}: resolves to {candidate!r}, which is "
            f"outside the mirror root {root!r}"
        )


def _safe_remove(root: Path, target: Path) -> None:
    """Delete `target`, but only ever inside `root`, never through a symlink."""
    _assert_inside_mirror(root, target)
    if target.is_symlink():
        # Unlink the link, never its target: the link may point out of the tree.
        target.unlink()
        return
    if target.is_dir():
        # shutil.rmtree uses scandir with follow_symlinks=False, so symlinked
        # directories encountered inside are unlinked rather than descended.
        shutil.rmtree(target)
        return
    target.unlink(missing_ok=True)


def _validated_unit_name(name: str) -> str:
    """A unit name becomes a directory name directly under the mirror root, so
    anything that could climb out of it is rejected rather than sanitised."""
    if not name or name in (".", ".."):
        raise ValueError(f"unusable skill name: {name!r}")
    if "/" in name or "\\" in name or "\0" in name:
        raise ValueError(
            f"refusing to mirror skill named {name!r}: a path separator in a "
            "name can escape the mirror root"
        )
    if Path(name).is_absolute() or Path(name).name != name:
        raise ValueError(f"refusing to mirror skill named {name!r}")
    return name


# --- mirror contents --------------------------------------------------------

def _redact(data: bytes, filename: str, secrets_config: dict) -> bytes:
    """Run bytes through redact.py.

    Imported lazily and deliberately un-guarded: if redaction is unavailable we
    must fail rather than copy unredacted files into corporate cloud storage.
    The late import also lets tests substitute a stub via sys.modules.
    """
    from redact import redact_bytes  # noqa: PLC0415 - see docstring
    redacted, _count = redact_bytes(data, filename, secrets_config)
    return redacted


def _desired_payload(unit: Unit, matcher, secrets_config: dict) -> _Payload:
    """The exact bytes the mirror should hold for one skill, already redacted.

    `matcher` is discovery's compiled excluder. Exclusions are tested against the
    unit-relative POSIX path only, never the absolute one: matching the absolute
    path would let a pattern like `**/cache/**` empty out an entire skill merely
    because the skill happens to live under some `.../cache/...` directory.
    """
    payload: _Payload = {}
    source = Path(unit.source_path)

    if unit.is_file:
        data = source.read_bytes()
        payload[source.name] = (
            _redact(data, source.name, secrets_config),
            bool(source.stat().st_mode & 0o111),
        )
        return payload

    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        here = Path(dirpath)
        kept = []
        for d in sorted(dirnames):
            child = here / d
            rel = child.relative_to(source).as_posix()
            # Symlinked directories are not descended: a skill that links to a
            # huge or cyclic tree must not blow up the mirror.
            if child.is_symlink() or matcher.excludes_dir(rel):
                continue
            kept.append(d)
        dirnames[:] = kept

        for f in sorted(filenames):
            path = here / f
            rel = path.relative_to(source).as_posix()
            if matcher.excludes(rel):
                continue
            try:
                data = path.read_bytes()
                executable = bool(path.stat().st_mode & 0o111)
            except OSError:
                # An unreadable file must not abort the whole mirror pass.
                continue
            payload[rel] = (_redact(data, path.name, secrets_config), executable)
    return payload


def _existing_state(target: Path) -> Tuple[_Payload, List[Path]]:
    """What the mirror currently holds for one skill.

    Returns (files, strays) where strays are symlinks that must be removed
    outright rather than compared.
    """
    files: _Payload = {}
    strays: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
        here = Path(dirpath)
        kept = []
        for d in sorted(dirnames):
            child = here / d
            if child.is_symlink():
                strays.append(child)
            else:
                kept.append(d)
        dirnames[:] = kept
        for f in sorted(filenames):
            path = here / f
            rel = path.relative_to(target).as_posix()
            if path.is_symlink():
                strays.append(path)
                continue
            try:
                files[rel] = (path.read_bytes(),
                              bool(path.stat().st_mode & 0o111))
            except OSError:
                strays.append(path)
    return files, strays


def _atomic_write(path: Path, data: bytes, executable: bool) -> None:
    """Write one mirrored file without ever exposing a half-written version to
    the OneDrive uploader."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o755 if executable else 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _prune_empty_dirs(root: Path, target: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(target, topdown=False,
                                                  followlinks=False):
        here = Path(dirpath)
        if here == target:
            continue
        try:
            next(here.iterdir())
        except StopIteration:
            _assert_inside_mirror(root, here)
            here.rmdir()
        except OSError:
            pass


def _sync_skill(root: Path, target: Path, desired: _Payload) -> bool:
    """Make `target` hold exactly `desired`. Returns True if anything changed.

    Only the differing files are rewritten -- a wholesale delete-and-recopy
    would re-upload every skill to OneDrive on every run.
    """
    existing, strays = _existing_state(target)
    changed = False

    for stray in strays:
        _safe_remove(root, stray)
        changed = True

    for rel, (data, executable) in sorted(desired.items()):
        if existing.get(rel) == (data, executable):
            continue
        _atomic_write(target / rel, data, executable)
        changed = True

    for rel in sorted(set(existing) - set(desired)):
        _safe_remove(root, target / rel)
        changed = True

    if changed:
        _prune_empty_dirs(root, target)
    return changed


def refresh_live_mirror(units: List[Unit], mirror_root: Path,
                        exclude_globs: List[str], secrets_config: dict) -> dict:
    """Refresh the browsable flat mirror to match current skills exactly.

    Only KIND_SKILL units are mirrored. Adds new, updates changed, removes skills
    that no longer exist. Redaction applies. Returns
    {'added': n, 'updated': n, 'removed': n}.

    Layout is one directory per skill directly under `mirror_root`, which is what
    the hand-maintained "Claude User Skills" folder already looks like.
    """
    root = _validated_mirror_root(mirror_root)
    matcher = _ExcludeMatcher(exclude_globs)

    wanted: Dict[str, Unit] = {}
    for unit in units:
        if unit.kind != KIND_SKILL:
            continue
        wanted[_validated_unit_name(unit.name)] = unit

    root.mkdir(parents=True, exist_ok=True)
    stats = {"added": 0, "updated": 0, "removed": 0}

    for name in sorted(wanted):
        unit = wanted[name]
        target = root / name
        _assert_inside_mirror(root, target)
        desired = _desired_payload(unit, matcher, secrets_config)

        if target.is_symlink() or (target.exists() and not target.is_dir()):
            # Whatever this is, it is not our skill directory. Replace it.
            _safe_remove(root, target)

        if not target.exists():
            for rel, (data, executable) in sorted(desired.items()):
                _atomic_write(target / rel, data, executable)
            target.mkdir(parents=True, exist_ok=True)  # no-op unless empty skill
            stats["added"] += 1
        elif _sync_skill(root, target, desired):
            stats["updated"] += 1

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            # OneDrive and Finder litter the folder with dotfiles. They are not
            # stale skills, so removing them is not this function's business.
            continue
        if entry.name in wanted:
            continue
        _safe_remove(root, entry)
        stats["removed"] += 1

    return stats


# --- index placement --------------------------------------------------------

def _sqlite_snapshot(db_path: Path, dest: Path) -> bool:
    """Copy the database with SQLite's online-backup API.

    index.py opens the index in WAL mode, so freshly committed rows can still
    live in index.db-wal. A plain byte copy of index.db would place a database
    in the cloud that is missing this run. Returns False when the source is not
    a SQLite database at all, so the caller can fall back to a byte copy.
    """
    src = None
    out = None
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        out = sqlite3.connect(str(dest))
        src.backup(out)
        out.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        for conn in (out, src):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
        # A WAL-mode destination leaves sidecar files behind; they are part of
        # the temp artefact, not of the placed database.
        for suffix in ("-wal", "-shm"):
            Path(str(dest) + suffix).unlink(missing_ok=True)


def place_index(db_path: Path, blob_root: Path) -> None:
    """Copy index.db beside objects/ in the active blob root, atomically.

    Temp file in the destination directory then os.replace: a reader (or the
    OneDrive uploader) sees either the old index or the new one, never a torn
    one. A stale index in the cloud is recoverable; a truncated one is not.
    """
    db_path = expand(db_path)
    blob_root = expand(blob_root)
    if not db_path.is_file():
        raise FileNotFoundError(f"no index database at {db_path}")

    blob_root.mkdir(parents=True, exist_ok=True)
    dest = blob_root / INDEX_NAME

    fd, tmp_name = tempfile.mkstemp(prefix=f".{INDEX_NAME}.", suffix=".tmp",
                                    dir=str(blob_root))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if not _sqlite_snapshot(db_path, tmp):
            with open(db_path, "rb") as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())
        os.replace(tmp, dest)
        dir_fd = os.open(str(blob_root), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --- launchd ----------------------------------------------------------------

def _plist_string(key: str, value: str) -> str:
    return f"\t<key>{_xml_escape(key)}</key>\n\t<string>{_xml_escape(value)}</string>"


def _calendar_dict(pairs: List[Tuple[str, int]]) -> str:
    inner = "".join(f"<key>{k}</key><integer>{v}</integer>" for k, v in pairs)
    return f"\t<key>StartCalendarInterval</key>\n\t<dict>{inner}</dict>"


def _schedule_block(interval_days: int, hour: int, minute: int) -> str:
    if interval_days == 1:
        return _calendar_dict([("Hour", hour), ("Minute", minute)])
    if interval_days == 7:
        # launchd Weekday 0 is Sunday.
        return _calendar_dict([("Weekday", 0), ("Hour", hour), ("Minute", minute)])
    return f"\t<key>StartInterval</key>\n\t<integer>{interval_days * 86400}</integer>"


def render_launchd_plist(config: dict, script_path: Path) -> str:
    """Return plist XML honouring schedule.interval_days/hour/minute.

    interval_days == 1  -> StartCalendarInterval daily at hour:minute
    interval_days == 7  -> StartCalendarInterval weekly (Weekday 0) at hour:minute
    otherwise           -> StartInterval in seconds
    Label from schedule.label. Follows com.acs.tide-sync.plist conventions:
    explicit ProgramArguments, EnvironmentVariables with HOME + a spelled-out
    PATH, RunAtLoad false, StandardOutPath == StandardErrorPath, ProcessType
    Background.
    """
    schedule = config.get("schedule", {})
    label = str(schedule.get("label", "com.acs.claude-skills-backup"))
    interval_days = int(schedule.get("interval_days", 7))
    hour = int(schedule.get("hour", 3))
    minute = int(schedule.get("minute", 0))

    if interval_days < 1:
        raise ValueError(f"schedule.interval_days must be >= 1, got {interval_days}")
    if not 0 <= hour <= 23:
        raise ValueError(f"schedule.hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"schedule.minute must be 0-59, got {minute}")

    home = Path.home()
    # ~/Library/Logs always exists; a StandardOutPath in a directory that does
    # not exist makes launchd fail to spawn the job with no obvious symptom.
    log_path = schedule.get("log_path") or (home / "Library" / "Logs" / f"{label}.log")
    log_path = Path(log_path).expanduser()
    launchd_path = f"{home / '.local' / 'bin'}:{_LAUNCHD_PATH_TAIL}"

    body = "\n\n".join([
        _plist_string("Label", label),
        "\t<key>ProgramArguments</key>\n\t<array>\n"
        + "\n".join(f"\t\t<string>{_xml_escape(a)}</string>"
                    for a in ("/usr/bin/python3", str(script_path), "run"))
        + "\n\t</array>",
        "\t<key>EnvironmentVariables</key>\n\t<dict>\n"
        f"\t\t<key>HOME</key>\n\t\t<string>{_xml_escape(str(home))}</string>\n"
        f"\t\t<key>PATH</key>\n\t\t<string>{_xml_escape(launchd_path)}</string>"
        "\n\t</dict>",
        _schedule_block(interval_days, hour, minute),
        # False, like tide-sync: loading or reloading the agent must not kick off
        # an unscheduled backup run.
        "\t<key>RunAtLoad</key>\n\t<false/>",
        _plist_string("StandardOutPath", str(log_path)),
        _plist_string("StandardErrorPath", str(log_path)),
        _plist_string("ProcessType", "Background"),
    ])

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"{body}\n"
        "</dict>\n"
        "</plist>\n"
    )
