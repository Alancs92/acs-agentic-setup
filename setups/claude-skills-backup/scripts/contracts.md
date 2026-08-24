# Module contracts — claude-skills-backup

**These signatures are frozen.** Every module is built independently against this
file. Do not change a signature without changing it here first; a mismatch breaks
integration silently.

Rules for every module:

- Python 3, **standard library only**. No third-party imports, ever.
- Module docstring stating purpose, inputs, outputs.
- Pure functions where possible; filesystem and DB effects confined to
  `store.py`, `index.py`, `sync.py`.
- All timestamps are **UTC ISO-8601 strings** ending in `Z`, second precision:
  `"2026-08-25T03:00:00Z"`. Never naive datetimes, never local time.
- Paths are `pathlib.Path`, always fully expanded (`~` resolved) before use.
- Tests live beside the module as `test_<module>.py`, stdlib `unittest`.

---

## Shared types (`backup_types.py` — owned by integrator, read-only for others)

> **Renamed from `types.py` (2026-08-25).** A module named `types.py` sitting in
> the scripts directory shadows the standard library's `types` module for every
> process whose `sys.path[0]` is that directory — which is every `python3
> backup.py`, every `python3 -m unittest`, and every direct test run. CPython's
> own `functools`/`enum`/`importlib` do `from types import GenericAlias`, hit our
> file instead, and the interpreter dies during startup before a single line of
> our code executes. Verified fatal on the local Python 3.9.6. Import the shared
> vocabulary as `from backup_types import Unit, FileEntry, Snapshot`.

```python
KIND_SKILL           = "skill"
KIND_COMMAND         = "command"
KIND_AGENT           = "agent"
KIND_HOOK            = "hook"
KIND_SETTINGS        = "settings"
KIND_CLAUDE_MD       = "claude_md"
KIND_PLUGIN_MANIFEST = "plugin_manifest"

ALL_KINDS = (KIND_SKILL, KIND_COMMAND, KIND_AGENT, KIND_HOOK,
             KIND_SETTINGS, KIND_CLAUDE_MD, KIND_PLUGIN_MANIFEST)


class Unit(NamedTuple):
    kind: str          # one of ALL_KINDS
    name: str          # unique within kind, e.g. "deep-research"
    source_path: Path  # resolved real path; symlinks already followed
    is_file: bool      # True for single-file units (settings, claude_md)


class FileEntry(NamedTuple):
    relpath: str       # POSIX-style, relative to the unit root, forward slashes
    executable: bool   # mode & 0o111
    sha256: str        # lowercase hex of file bytes


class Snapshot(NamedTuple):
    id: int
    unit_id: int
    content_hash: str
    blob_key: str
    size_bytes: int
    file_count: int
    created_utc: str
    run_id: int
    pruned_utc: str | None
```

`utcnow_iso() -> str` also lives in `backup_types.py`. **Every module takes the current
time as an argument** rather than calling it directly, so tests are deterministic.

---

## `discovery.py` — enumerate units

```python
def discover(config: dict) -> list[Unit]:
    """Enumerate every enabled unit from config['sources'].

    Symlinks are resolved to their real target; a symlinked skill is backed up by
    content, not as a link. Units are returned sorted by (kind, name).
    Unreadable sources are skipped, never fatal.
    """

def iter_files(unit: Unit, exclude_globs: list[str]) -> list[Path]:
    """Every file belonging to a unit, sorted, exclusions applied.

    For is_file units this is a single-element list.
    """
```

**Correction 2026-08-25 — do NOT use `PurePath.match` or `fnmatch` for
`exclude_globs`.** Both are wrong for the shipped patterns and fail *open*:
on Python < 3.13 `PurePosixPath('.git/config').match('**/.git/**')` is `False`,
and `fnmatch`'s `*` happily crosses `/`. Either one silently admits entire `.git`
trees into blobs that go to OneDrive. `discovery.py` therefore carries a small
gitignore-flavoured glob→regex compiler: `**/` matches zero or more leading
directories, `*`/`?` never cross `/`, and a slash-less pattern matches at any
depth. Reuse that compiler rather than reaching for the stdlib matchers.

It is exported as `discovery.Matcher` (`.excludes(relpath)` /
`.excludes_dir(relpath)`) and is the **single glob engine** for `exclude_globs`.
`sync.py` imports it so the live mirror and the blob store cannot disagree about
what is excluded. Match against the **unit-relative** path only, never the
absolute one: `storage.local_fallback` is `~/.cache/claude-acs-backup`, so a
pattern like `**/cache/**` matched absolutely would silently empty the mirror
after any restore-then-rebackup cycle.

Plugin handling: with `plugins.mode == "manifests"`, emit one
`KIND_PLUGIN_MANIFEST` unit per top-level `*.json` in the plugins dir, named after
the file stem. Never descend into `cache/` or `marketplaces/`. With `"full"`,
emit a single unit covering the whole tree.

Settings handling: for each account in `sources.settings.accounts`, emit
`KIND_SETTINGS` units named `<account>/settings.json` and
`<account>/settings.local.json` when present, plus a `KIND_CLAUDE_MD` unit named
`<account>/CLAUDE.md`.

---

## `hashing.py` — canonical content hash

```python
def file_entries(unit: Unit, files: list[Path],
                 secrets_config: dict | None = None) -> list[FileEntry]:
    """Build the sorted FileEntry manifest for a unit.

    When secrets_config is provided, each file's bytes are passed through
    redact.redact_bytes BEFORE hashing, so the manifest describes the bytes that
    will actually be archived. Without it, behaviour is unchanged (raw bytes).
    """

def content_hash(entries: list[FileEntry]) -> str:
    """Canonical sha256 over the manifest. Lowercase hex.

    Hashed input is, for each entry sorted by relpath:
        f"{relpath}\\n{int(executable)}\\n{sha256}\\n"
    concatenated and UTF-8 encoded.

    CRITICAL: mtime, ctime, uid, gid and absolute paths are excluded. Including
    any of them makes a touch or a OneDrive resync fabricate a snapshot every
    cycle, which defeats the whole point of change-detection.
    """
```

**Hash the STORED bytes, not the on-disk bytes.** `store.write_blob` redacts every
file on its way into the tar, so a manifest built from raw bytes yields a
`content_hash` that does not match the blob it names — and `verify_blob`, which
extracts and recomputes, then reports the unit corrupt forever. Latent until the
first token appears in `settings.json`, permanent afterwards. Callers that will
store the result (`backup.py`) MUST pass `secrets_config`.

This is correct rather than a workaround: all three consumers of the hash want
the stored bytes — blob addressing and dedup key on stored content, verify
recomputes from stored content, and change detection survives because the
redaction marker embeds a sha256 prefix of the original value, so rotating a
token still moves the hash. Raw-byte hashing is the odd one out.

The `filename` handed to `redact_bytes` is the unit-relative POSIX relpath
(bare basename for `is_file` units) — computed by the same rule as
`store._member_name`, so JSON detection cannot diverge between what is hashed
and what is written. `redact.looks_like_json(filename)` is the shared predicate,
which also keeps non-JSON files on the bounded-chunk read path. `redact` is
imported lazily inside the function so `import hashing` stands alone; the
`ImportError` is deliberately not caught, because silently hashing and storing
unredacted bytes would put a live token in corporate OneDrive.

---

## `redact.py` — strip secrets before storage

```python
def redact_bytes(data: bytes, filename: str, secrets_config: dict) -> tuple[bytes, int]:
    """Return (possibly-redacted bytes, count of redactions).

    Only JSON files are inspected; anything else passes through untouched.
    A value is redacted when its KEY matches secrets_config['key_patterns']
    (case-insensitive substring) or its VALUE matches any of
    secrets_config['value_patterns'] (regex).

    Replacement is «REDACTED:<first 12 hex of sha256(value)>», which preserves
    change-detection without carrying the secret. Applies recursively through
    nested objects and arrays. Malformed JSON returns unchanged with count 0.
    """
```

---

## `index.py` — SQLite index

```python
def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating parents), apply schema if absent, set WAL + foreign_keys."""

def start_run(conn, now: str) -> int: ...
def finish_run(conn, run_id: int, now: str, status: str,
               stats: dict, error: str | None = None) -> None: ...

def upsert_unit(conn, unit: Unit, now: str) -> int:
    """Insert or touch a unit; always bumps last_seen_utc. Clears deleted_utc on
    reappearance. Returns unit_id."""

def mark_missing_units(conn, seen_unit_ids: list[int], now: str,
                       enabled_kinds: Iterable[str] | None = None) -> int:
    """Set deleted_utc on units of an enabled kind that were not seen this run.
    Returns the number newly tombstoned.

    enabled_kinds: the kinds actually SCANNED this run — backup.py reads them
      from config. Supplying them is what makes "kind enabled, every unit
      deleted" detectable: a fully-emptied kind and a never-scanned kind produce
      identical evidence (no seen ids), so inference cannot tell them apart. A
      kind listed here with zero seen units DOES tombstone all of its units.

    When None, scope falls back to the kinds present among seen_unit_ids. That
    preserves the fail-safe for a partial or aborted scan: no seen ids and no
    declared scope tombstones nothing. An explicitly empty enabled_kinds means
    "nothing was scanned" and likewise marks nothing.

    Already-tombstoned units keep their original deleted_utc and are not
    counted again.
    """

def latest_snapshot(conn, unit_id: int) -> Snapshot | None:
    """Newest non-pruned snapshot, or None."""

def record_snapshot(conn, unit_id: int, content_hash: str, blob_key: str,
                    size_bytes: int, file_count: int, run_id: int,
                    now: str) -> int: ...

def snapshots_for_unit(conn, unit_id: int, include_pruned: bool = True) -> list[Snapshot]:
    """Newest first."""

def mark_pruned(conn, snapshot_ids: list[int], now: str) -> None:
    """Tombstone: sets pruned_utc, keeps the row."""

def hash_is_referenced(conn, content_hash: str) -> bool:
    """True if any non-pruned snapshot still points at this hash. Blobs must not
    be unlinked while this is True — dedup lets units share a blob."""

def export_catalog(conn, out_path: Path) -> int:
    """Write catalog.jsonl, one line per snapshot, sorted by (kind, name,
    created_utc). Keys in fixed order: kind, name, hash, created, bytes, files,
    pruned. Returns rows written. Output must be byte-stable across runs given
    the same DB — it is committed to git and must diff cleanly."""
```

**Amendment 2026-08-25** — `mark_missing_units` gained the optional
`enabled_kinds` parameter shown above. Backwards compatible: existing three-arg
calls keep the inferred-scope behaviour. `backup.py` should pass the enabled
kinds from config so a kind whose units are ALL deleted in one run is still
detected — inference cannot see that case.

**Amendment 2026-08-25** — three read helpers the CLI needs:

```python
def iter_unit_ids(conn) -> list[tuple[int, str, str]]:
    """Every unit as (unit_id, kind, name), sorted by (kind, name). Includes
    units with deleted_utc set — their snapshot history is still restorable."""

def last_run(conn) -> dict | None:
    """Most recent run as a dict with keys: id, started_utc, finished_utc,
    status, units_scanned, snapshots_created, blobs_pruned, bytes_written,
    bytes_reclaimed, error. None when no runs exist."""

def summary_counts(conn) -> dict:
    """{'live_units': n, 'deleted_units': n, 'live_snapshots': n,
        'tombstones': n} — one pass, for `backup status`."""
```

Schema is exactly as in `docs/superpowers/specs/2026-08-25-claude-skills-backup-design.md`,
with the SQL comments retained.

---

## `store.py` — content-addressed blob store

```python
def blob_key(content_hash: str) -> str:
    """'objects/<h[0:2]>/<h[2:4]>/<h>.tar.xz' — POSIX separators always."""

def write_blob(root: Path, content_hash: str, unit: Unit, files: list[Path],
               secrets_config: dict) -> tuple[str, int]:
    """Write the unit's files as a tar.xz at blob_key(content_hash) under root.

    Returns (blob_key, size_bytes). If the blob already exists, returns without
    rewriting — that is the dedup path.

    Redaction runs per-file via redact.redact_bytes BEFORE the byte enters the
    archive. Nothing unredacted may ever reach the archive; these blobs go to
    corporate OneDrive.

    Archive member names are the unit-relative POSIX paths. tarfile mtime is
    forced to 0 and uid/gid/uname/gname cleared, so the archive is reproducible.
    Written atomically: temp file then os.replace.
    """

def read_blob(root: Path, blob_key: str, dest: Path) -> int:
    """Extract to dest, returns file count. Rejects member paths that escape dest
    (path traversal guard) — refuse absolute paths and any '..' component."""

def delete_blob(root: Path, blob_key: str) -> int:
    """Unlink, return bytes reclaimed. Missing blob returns 0, never raises.
    Prunes now-empty parent shard dirs."""

def verify_blob(root: Path, blob_key: str, expected_hash: str) -> bool:
    """Extract to temp, recompute the canonical content hash, compare."""
```

---

## `retention.py` — pure policy engine

```python
def select_prunable(snapshots: list[Snapshot], policy: dict, now: str) -> list[int]:
    """Given one unit's snapshots (any order) return snapshot ids to prune.

    Pure. No filesystem, no DB, no clock — `now` is passed in.
    Already-pruned snapshots are ignored (never returned twice).

    Rules, applied in this order:
      1. The newest `always_keep_latest` are ALWAYS kept.
      2. If `never_delete_only_copy`, the unit always retains at least one live
         snapshot — a FLOOR, not a count-of-one check. It applies to the result
         of all other rules, including stability collapse, so a hand-edited
         `always_keep_latest: 0` + `stable_keep: 0` still leaves the newest one
         standing. Amended 2026-08-25: the earlier count-of-one wording let a
         unit go from N snapshots straight to zero.
      3. Stability collapse: if the NEWEST snapshot is older than
         `stable_after_days`, the unit has settled — keep only the newest
         `stable_keep`, prune the rest. Skip remaining rules.
      4. Snapshots newer than `keep_all_within_days` are all kept.
      5. For each tier, oldest-applicable wins: within the tier's age band keep
         one snapshot per bucket (`weekly` = ISO year+week of created_utc,
         `monthly` = year+month), keeping the NEWEST in each bucket.
      6. Anything not kept by the above is pruned.
    """
```

This module must have no imports beyond `datetime` and the shared types. It is
the piece most likely to silently destroy data, so it is pure and
table-driven-tested.

---

## `sync.py` — OneDrive placement

```python
def resolve_storage(config: dict) -> tuple[Path, bool]:
    """Return (active blob root, is_degraded).

    Prefers storage.onedrive_root. Falls back to storage.local_fallback when the
    OneDrive path is absent or not writable — degraded, never fatal.
    """

def refresh_live_mirror(units: list[Unit], mirror_root: Path,
                        exclude_globs: list[str], secrets_config: dict) -> dict:
    """Refresh the browsable flat mirror to match current skills exactly.

    Only KIND_SKILL units are mirrored. Adds new, updates changed, removes skills
    that no longer exist. Redaction applies. Returns
    {'added': n, 'updated': n, 'removed': n}.
    """

def place_index(db_path: Path, blob_root: Path) -> None:
    """Copy index.db beside objects/ in the active blob root, atomically."""

def render_launchd_plist(config: dict, script_path: Path) -> str:
    """Return plist XML honouring schedule.interval_days/hour/minute.

    interval_days == 1  -> StartCalendarInterval daily at hour:minute
    interval_days == 7  -> StartCalendarInterval weekly (Weekday 0) at hour:minute
    otherwise           -> StartInterval in seconds
    Label from schedule.label. Follows com.acs.tide-sync.plist conventions.
    """
```

---

## `backup.py` — CLI orchestration (integrator-owned)

Subcommands: `run`, `status`, `list`, `restore`, `prune`, `verify`,
`install-schedule`. Wired into `claude-acs backup` through
`_claude_acs_setup_script`, mirroring `_claude_acs_stats`.
