#!/usr/bin/env python3
"""Tests for index.py -- the SQLite index of claude-skills-backup.

Written before the implementation. The cases that carry weight are the ones
where a bug destroys data silently instead of raising:

  * schema idempotency   -- connect() on an existing DB must not wipe it
  * unit resurrection    -- a returning unit must lose its deleted_utc
  * tombstone survival   -- mark_pruned must never DELETE a row
  * shared-blob refcount -- dedup lets two units share one blob; unlinking it
                            while either still references it is data loss
  * catalog stability    -- catalog.jsonl is committed to git, so two exports of
                            the same DB must be byte-identical

Every test uses a real on-disk DB in a temp dir rather than ':memory:', because
connect()'s parent-dir creation and WAL mode are part of what is under test.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import index
from index import (
    KIND_COMMAND,
    KIND_SETTINGS,
    KIND_SKILL,
    STATUS_DEGRADED,
    STATUS_OK,
    Snapshot,
    Unit,
)

T0 = "2026-01-01T00:00:00Z"
T1 = "2026-02-01T00:00:00Z"
T2 = "2026-03-01T00:00:00Z"
T3 = "2026-04-01T00:00:00Z"

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_SHARED = "c" * 64


def mkunit(name, kind=KIND_SKILL, path=None, is_file=False):
    return Unit(
        kind=kind,
        name=name,
        source_path=Path(path or f"/tmp/claude/{kind}/{name}"),
        is_file=is_file,
    )


class IndexTestCase(unittest.TestCase):
    """Common fixture: a temp dir and a connected DB."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "nested" / "dirs" / "index.db"
        self.conn = index.connect(self.db_path)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.conn.close)

    def deleted_count(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM units WHERE deleted_utc IS NOT NULL"
        ).fetchone()[0]

    def snap(self, unit_id, run_id, content_hash, created, size=100, files=3):
        return index.record_snapshot(
            self.conn,
            unit_id=unit_id,
            content_hash=content_hash,
            blob_key="objects/%s/%s/%s.tar.xz"
            % (content_hash[0:2], content_hash[2:4], content_hash),
            size_bytes=size,
            file_count=files,
            run_id=run_id,
            now=created,
        )


class TestConnect(IndexTestCase):
    def test_creates_parent_directories(self):
        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.db_path.parent.is_dir())

    def test_schema_tables_created(self):
        names = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertLessEqual({"units", "snapshots", "runs", "schema_meta"}, names)

    def test_schema_version_recorded(self):
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(str(index.SCHEMA_VERSION), row[0])

    def test_foreign_keys_pragma_on(self):
        self.assertEqual(1, self.conn.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_wal_mode_enabled(self):
        mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual("wal", mode.lower())

    def test_foreign_keys_are_actually_enforced(self):
        # A snapshot pointing at a non-existent unit must be rejected. If this
        # passes silently the FK pragma was never applied.
        with self.assertRaises(sqlite3.IntegrityError):
            self.snap(unit_id=9999, run_id=1, content_hash=HASH_A, created=T0)

    def test_idempotent_reconnect_preserves_data(self):
        run_id = index.start_run(self.conn, T0)
        unit_id = index.upsert_unit(self.conn, mkunit("deep-research"), T0)
        snap_id = self.snap(unit_id, run_id, HASH_A, T0)
        index.finish_run(self.conn, run_id, T1, STATUS_OK, {})
        self.conn.close()

        conn2 = index.connect(self.db_path)
        self.addCleanup(conn2.close)
        self.assertEqual(
            1, conn2.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        )
        self.assertEqual(
            snap_id,
            conn2.execute("SELECT id FROM snapshots").fetchone()[0],
        )
        self.assertEqual(
            1, conn2.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        )
        # And the version marker is not duplicated.
        self.assertEqual(
            1,
            conn2.execute(
                "SELECT COUNT(*) FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0],
        )

    def test_connect_accepts_str_path(self):
        conn = index.connect(str(self.tmp / "other" / "i.db"))
        self.addCleanup(conn.close)
        self.assertTrue((self.tmp / "other" / "i.db").exists())


class TestRuns(IndexTestCase):
    def test_start_run_returns_increasing_ids_and_records_start(self):
        a = index.start_run(self.conn, T0)
        b = index.start_run(self.conn, T1)
        self.assertIsInstance(a, int)
        self.assertLess(a, b)
        started = self.conn.execute(
            "SELECT started_utc FROM runs WHERE id=?", (a,)
        ).fetchone()[0]
        self.assertEqual(T0, started)
        self.assertIsNone(
            self.conn.execute(
                "SELECT finished_utc FROM runs WHERE id=?", (a,)
            ).fetchone()[0]
        )

    def test_finish_run_writes_stats_and_status(self):
        run_id = index.start_run(self.conn, T0)
        index.finish_run(
            self.conn,
            run_id,
            T1,
            STATUS_DEGRADED,
            {
                "units_scanned": 12,
                "snapshots_created": 3,
                "blobs_pruned": 1,
                "bytes_written": 4096,
                "bytes_reclaimed": 512,
            },
            error="onedrive unavailable",
        )
        row = self.conn.execute(
            "SELECT finished_utc, units_scanned, snapshots_created, blobs_pruned,"
            " bytes_written, bytes_reclaimed, status, error FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        self.assertEqual(
            (T1, 12, 3, 1, 4096, 512, STATUS_DEGRADED, "onedrive unavailable"),
            tuple(row),
        )

    def test_finish_run_defaults_missing_stats_to_zero(self):
        run_id = index.start_run(self.conn, T0)
        index.finish_run(self.conn, run_id, T1, STATUS_OK, {})
        row = self.conn.execute(
            "SELECT units_scanned, snapshots_created, blobs_pruned, bytes_written,"
            " bytes_reclaimed, error FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        self.assertEqual((0, 0, 0, 0, 0, None), tuple(row))

    def test_full_run_lifecycle(self):
        run_id = index.start_run(self.conn, T0)
        unit_id = index.upsert_unit(self.conn, mkunit("deep-research"), T0)
        snap_id = self.snap(unit_id, run_id, HASH_A, T0, size=2048, files=7)
        index.finish_run(
            self.conn,
            run_id,
            T1,
            STATUS_OK,
            {"units_scanned": 1, "snapshots_created": 1, "bytes_written": 2048},
        )

        latest = index.latest_snapshot(self.conn, unit_id)
        self.assertIsInstance(latest, Snapshot)
        self.assertEqual(snap_id, latest.id)
        self.assertEqual(unit_id, latest.unit_id)
        self.assertEqual(HASH_A, latest.content_hash)
        self.assertEqual(2048, latest.size_bytes)
        self.assertEqual(7, latest.file_count)
        self.assertEqual(T0, latest.created_utc)
        self.assertEqual(run_id, latest.run_id)
        self.assertIsNone(latest.pruned_utc)
        self.assertEqual(
            "objects/%s/%s/%s.tar.xz" % (HASH_A[0:2], HASH_A[2:4], HASH_A),
            latest.blob_key,
        )


class TestUnits(IndexTestCase):
    def test_insert_then_touch_keeps_same_id(self):
        u = mkunit("deep-research")
        first = index.upsert_unit(self.conn, u, T0)
        second = index.upsert_unit(self.conn, u, T1)
        self.assertEqual(first, second)
        self.assertEqual(
            1, self.conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        )

    def test_upsert_bumps_last_seen_but_not_first_seen(self):
        u = mkunit("deep-research")
        unit_id = index.upsert_unit(self.conn, u, T0)
        index.upsert_unit(self.conn, u, T2)
        first_seen, last_seen = self.conn.execute(
            "SELECT first_seen_utc, last_seen_utc FROM units WHERE id=?", (unit_id,)
        ).fetchone()
        self.assertEqual(T0, first_seen)
        self.assertEqual(T2, last_seen)

    def test_same_name_different_kind_are_distinct_units(self):
        a = index.upsert_unit(self.conn, mkunit("review", kind=KIND_SKILL), T0)
        b = index.upsert_unit(self.conn, mkunit("review", kind=KIND_COMMAND), T0)
        self.assertNotEqual(a, b)

    def test_upsert_updates_source_path_when_it_moves(self):
        unit_id = index.upsert_unit(self.conn, mkunit("s", path="/old/s"), T0)
        index.upsert_unit(self.conn, mkunit("s", path="/new/s"), T1)
        path = self.conn.execute(
            "SELECT source_path FROM units WHERE id=?", (unit_id,)
        ).fetchone()[0]
        self.assertEqual("/new/s", path)

    def test_resurrection_clears_deleted_utc(self):
        # A skill vanishes, is marked deleted, then comes back. The row must
        # stop looking deleted -- otherwise retention and status both lie.
        u = mkunit("comeback-kid")
        anchor = index.upsert_unit(self.conn, mkunit("still-here"), T0)
        unit_id = index.upsert_unit(self.conn, u, T0)

        index.mark_missing_units(self.conn, [anchor], T1)
        self.assertEqual(
            T1,
            self.conn.execute(
                "SELECT deleted_utc FROM units WHERE id=?", (unit_id,)
            ).fetchone()[0],
        )

        again = index.upsert_unit(self.conn, u, T2)
        self.assertEqual(unit_id, again)
        deleted, last_seen = self.conn.execute(
            "SELECT deleted_utc, last_seen_utc FROM units WHERE id=?", (unit_id,)
        ).fetchone()
        self.assertIsNone(deleted, "resurrected unit still carries deleted_utc")
        self.assertEqual(T2, last_seen)

    def test_mark_missing_marks_only_unseen_units_of_seen_kinds(self):
        kept = index.upsert_unit(self.conn, mkunit("kept"), T0)
        gone = index.upsert_unit(self.conn, mkunit("gone"), T0)
        other_kind = index.upsert_unit(
            self.conn, mkunit("settings.json", kind=KIND_SETTINGS, is_file=True), T0
        )

        count = index.mark_missing_units(self.conn, [kept], T1)
        self.assertEqual(1, count)

        deleted = dict(
            self.conn.execute("SELECT id, deleted_utc FROM units").fetchall()
        )
        self.assertIsNone(deleted[kept])
        self.assertEqual(T1, deleted[gone])
        # The settings kind was not scanned this run (no seen ids of that kind),
        # so its units must NOT be declared deleted.
        self.assertIsNone(deleted[other_kind])

    def test_mark_missing_is_not_recounted_on_a_second_run(self):
        index.upsert_unit(self.conn, mkunit("kept"), T0)
        index.upsert_unit(self.conn, mkunit("gone"), T0)
        kept = index.upsert_unit(self.conn, mkunit("kept"), T0)

        self.assertEqual(1, index.mark_missing_units(self.conn, [kept], T1))
        self.assertEqual(0, index.mark_missing_units(self.conn, [kept], T2))
        deleted = self.conn.execute(
            "SELECT deleted_utc FROM units WHERE name='gone'"
        ).fetchone()[0]
        self.assertEqual(T1, deleted, "original deletion time was overwritten")

    def test_mark_missing_with_no_seen_units_marks_nothing(self):
        # Defensive: an aborted scan (no ids, no declared scope) must never
        # mass-delete the index.
        index.upsert_unit(self.conn, mkunit("a"), T0)
        index.upsert_unit(self.conn, mkunit("b"), T0)
        self.assertEqual(0, index.mark_missing_units(self.conn, [], T1))
        self.assertEqual(0, self.deleted_count())

    def test_aborted_scan_with_enabled_kinds_none_tombstones_nothing(self):
        # Explicit restatement of the fail-safe now that the None branch is a
        # documented fallback rather than the only behaviour.
        index.upsert_unit(self.conn, mkunit("a"), T0)
        index.upsert_unit(self.conn, mkunit("b", kind=KIND_COMMAND), T0)
        self.assertEqual(0, index.mark_missing_units(self.conn, [], T1, None))
        self.assertEqual(0, self.deleted_count())

    def test_enabled_kind_emptied_completely_is_tombstoned(self):
        # The case inference structurally cannot see: the kind WAS scanned, and
        # every unit in it is gone. A backup that misses this never notices a
        # wiped-out skills directory.
        index.upsert_unit(self.conn, mkunit("gone-1"), T0)
        index.upsert_unit(self.conn, mkunit("gone-2"), T0)

        count = index.mark_missing_units(
            self.conn, [], T1, enabled_kinds=[KIND_SKILL]
        )
        self.assertEqual(2, count)
        self.assertEqual(2, self.deleted_count())
        self.assertEqual(
            [T1, T1],
            [
                row[0]
                for row in self.conn.execute(
                    "SELECT deleted_utc FROM units ORDER BY name"
                )
            ],
        )

    def test_enabled_kinds_scopes_to_the_declared_kinds_only(self):
        skill_kept = index.upsert_unit(self.conn, mkunit("kept"), T0)
        skill_gone = index.upsert_unit(self.conn, mkunit("gone"), T0)
        cmd_gone = index.upsert_unit(self.conn, mkunit("c", kind=KIND_COMMAND), T0)
        settings = index.upsert_unit(
            self.conn, mkunit("settings.json", kind=KIND_SETTINGS, is_file=True), T0
        )

        # Skills and commands were scanned; settings was not enabled this run.
        count = index.mark_missing_units(
            self.conn, [skill_kept], T1, enabled_kinds=(KIND_SKILL, KIND_COMMAND)
        )
        self.assertEqual(2, count)
        deleted = dict(
            self.conn.execute("SELECT id, deleted_utc FROM units").fetchall()
        )
        self.assertIsNone(deleted[skill_kept])
        self.assertEqual(T1, deleted[skill_gone])
        self.assertEqual(T1, deleted[cmd_gone])
        self.assertIsNone(
            deleted[settings], "a kind that was not scanned was declared deleted"
        )

    def test_empty_enabled_kinds_means_nothing_was_scanned(self):
        index.upsert_unit(self.conn, mkunit("a"), T0)
        self.assertEqual(0, index.mark_missing_units(self.conn, [], T1, []))
        self.assertEqual(0, self.deleted_count())

    def test_enabled_kinds_accepts_any_iterable_and_ignores_duplicates(self):
        kept = index.upsert_unit(self.conn, mkunit("kept"), T0)
        index.upsert_unit(self.conn, mkunit("gone"), T0)
        count = index.mark_missing_units(
            self.conn, [kept], T1, enabled_kinds=iter([KIND_SKILL, KIND_SKILL])
        )
        self.assertEqual(1, count)

    def test_enabled_kinds_does_not_recount_already_tombstoned_units(self):
        index.upsert_unit(self.conn, mkunit("gone"), T0)
        self.assertEqual(
            1, index.mark_missing_units(self.conn, [], T1, [KIND_SKILL])
        )
        self.assertEqual(
            0, index.mark_missing_units(self.conn, [], T2, [KIND_SKILL])
        )
        self.assertEqual(
            T1,
            self.conn.execute(
                "SELECT deleted_utc FROM units WHERE name='gone'"
            ).fetchone()[0],
        )

    def test_resurrection_after_a_whole_kind_was_emptied(self):
        # Emptied kind comes back (e.g. a synced folder finished re-populating).
        u = mkunit("comeback-kid")
        unit_id = index.upsert_unit(self.conn, u, T0)
        index.mark_missing_units(self.conn, [], T1, [KIND_SKILL])
        self.assertEqual(1, self.deleted_count())

        self.assertEqual(unit_id, index.upsert_unit(self.conn, u, T2))
        self.assertEqual(0, self.deleted_count())


class TestCliReadHelpers(IndexTestCase):
    def test_iter_unit_ids_empty(self):
        self.assertEqual([], index.iter_unit_ids(self.conn))

    def test_iter_unit_ids_sorted_by_kind_then_name(self):
        index.upsert_unit(self.conn, mkunit("zulu"), T0)
        index.upsert_unit(self.conn, mkunit("alpha"), T0)
        index.upsert_unit(self.conn, mkunit("mike", kind=KIND_COMMAND), T0)
        got = index.iter_unit_ids(self.conn)
        self.assertEqual(
            [(KIND_COMMAND, "mike"), (KIND_SKILL, "alpha"), (KIND_SKILL, "zulu")],
            [(kind, name) for _id, kind, name in got],
        )
        self.assertTrue(all(isinstance(row[0], int) for row in got))

    def test_iter_unit_ids_includes_deleted_units(self):
        # A deleted unit's snapshot history is still restorable -- dropping it
        # from this listing would make `backup restore` unable to reach it.
        kept = index.upsert_unit(self.conn, mkunit("kept"), T0)
        gone = index.upsert_unit(self.conn, mkunit("gone"), T0)
        index.mark_missing_units(self.conn, [kept], T1)
        self.assertIn(gone, [row[0] for row in index.iter_unit_ids(self.conn)])

    def test_last_run_none_when_no_runs(self):
        self.assertIsNone(index.last_run(self.conn))

    def test_last_run_returns_the_most_recent(self):
        index.start_run(self.conn, T0)
        second = index.start_run(self.conn, T1)
        index.finish_run(
            self.conn,
            second,
            T2,
            STATUS_OK,
            {"units_scanned": 5, "snapshots_created": 2, "blobs_pruned": 1,
             "bytes_written": 64, "bytes_reclaimed": 8},
        )
        got = index.last_run(self.conn)
        self.assertEqual(
            {
                "id": second,
                "started_utc": T1,
                "finished_utc": T2,
                "status": STATUS_OK,
                "units_scanned": 5,
                "snapshots_created": 2,
                "blobs_pruned": 1,
                "bytes_written": 64,
                "bytes_reclaimed": 8,
                "error": None,
            },
            got,
        )

    def test_last_run_reports_an_unfinished_run(self):
        run_id = index.start_run(self.conn, T0)
        got = index.last_run(self.conn)
        self.assertEqual(run_id, got["id"])
        self.assertIsNone(got["finished_utc"])
        self.assertIsNone(got["status"])

    def test_summary_counts_on_an_empty_db(self):
        self.assertEqual(
            {
                "live_units": 0,
                "deleted_units": 0,
                "live_snapshots": 0,
                "tombstones": 0,
            },
            index.summary_counts(self.conn),
        )

    def test_summary_counts(self):
        run_id = index.start_run(self.conn, T0)
        kept = index.upsert_unit(self.conn, mkunit("kept"), T0)
        other = index.upsert_unit(self.conn, mkunit("other"), T0)
        gone = index.upsert_unit(self.conn, mkunit("gone"), T0)
        live = self.snap(kept, run_id, HASH_A, T0)
        self.snap(other, run_id, HASH_B, T1)
        dead = self.snap(gone, run_id, HASH_SHARED, T1)
        index.mark_missing_units(self.conn, [kept, other], T2)
        index.mark_pruned(self.conn, [dead], T2)

        self.assertEqual(
            {
                "live_units": 2,
                "deleted_units": 1,
                "live_snapshots": 2,
                "tombstones": 1,
            },
            index.summary_counts(self.conn),
        )
        self.assertIsNotNone(index.latest_snapshot(self.conn, kept))
        self.assertEqual(live, index.latest_snapshot(self.conn, kept).id)


class TestSnapshots(IndexTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = index.start_run(self.conn, T0)
        self.unit_id = index.upsert_unit(self.conn, mkunit("deep-research"), T0)

    def test_latest_snapshot_none_when_empty(self):
        self.assertIsNone(index.latest_snapshot(self.conn, self.unit_id))

    def test_latest_snapshot_is_newest(self):
        self.snap(self.unit_id, self.run_id, HASH_A, T0)
        newer = self.snap(self.unit_id, self.run_id, HASH_B, T2)
        self.assertEqual(newer, index.latest_snapshot(self.conn, self.unit_id).id)

    def test_latest_snapshot_skips_pruned(self):
        older = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        newer = self.snap(self.unit_id, self.run_id, HASH_B, T2)
        index.mark_pruned(self.conn, [newer], T3)
        latest = index.latest_snapshot(self.conn, self.unit_id)
        self.assertEqual(older, latest.id)

    def test_latest_snapshot_none_when_all_pruned(self):
        a = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        b = self.snap(self.unit_id, self.run_id, HASH_B, T2)
        index.mark_pruned(self.conn, [a, b], T3)
        self.assertIsNone(index.latest_snapshot(self.conn, self.unit_id))

    def test_snapshots_for_unit_newest_first(self):
        oldest = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        middle = self.snap(self.unit_id, self.run_id, HASH_B, T1)
        newest = self.snap(self.unit_id, self.run_id, HASH_SHARED, T2)
        got = index.snapshots_for_unit(self.conn, self.unit_id)
        self.assertEqual([newest, middle, oldest], [s.id for s in got])
        self.assertTrue(all(isinstance(s, Snapshot) for s in got))

    def test_snapshots_for_unit_is_scoped_to_the_unit(self):
        other = index.upsert_unit(self.conn, mkunit("other"), T0)
        mine = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        self.snap(other, self.run_id, HASH_B, T0)
        self.assertEqual(
            [mine], [s.id for s in index.snapshots_for_unit(self.conn, self.unit_id)]
        )

    def test_snapshots_for_unit_can_exclude_pruned(self):
        live = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        dead = self.snap(self.unit_id, self.run_id, HASH_B, T1)
        index.mark_pruned(self.conn, [dead], T3)
        self.assertEqual(
            [dead, live],
            [s.id for s in index.snapshots_for_unit(self.conn, self.unit_id)],
        )
        self.assertEqual(
            [live],
            [
                s.id
                for s in index.snapshots_for_unit(
                    self.conn, self.unit_id, include_pruned=False
                )
            ],
        )

    def test_snapshots_with_identical_timestamps_are_ordered_by_id(self):
        a = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        b = self.snap(self.unit_id, self.run_id, HASH_B, T0)
        self.assertEqual(
            [b, a], [s.id for s in index.snapshots_for_unit(self.conn, self.unit_id)]
        )


class TestPruning(IndexTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = index.start_run(self.conn, T0)
        self.unit_id = index.upsert_unit(self.conn, mkunit("deep-research"), T0)

    def test_mark_pruned_is_a_tombstone_the_row_survives(self):
        snap_id = self.snap(self.unit_id, self.run_id, HASH_A, T0, size=99, files=4)
        index.mark_pruned(self.conn, [snap_id], T3)

        row = self.conn.execute(
            "SELECT id, unit_id, content_hash, blob_key, size_bytes, file_count,"
            " created_utc, pruned_utc FROM snapshots WHERE id=?",
            (snap_id,),
        ).fetchone()
        self.assertIsNotNone(row, "mark_pruned DELETED the row -- history is lost")
        self.assertEqual(HASH_A, row[2])
        self.assertEqual(99, row[4])
        self.assertEqual(4, row[5])
        self.assertEqual(T0, row[6])
        self.assertEqual(T3, row[7])
        self.assertEqual(
            1, self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        )

    def test_mark_pruned_empty_list_is_a_noop(self):
        snap_id = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        index.mark_pruned(self.conn, [], T3)
        self.assertIsNotNone(index.latest_snapshot(self.conn, self.unit_id))
        self.assertIsNone(
            self.conn.execute(
                "SELECT pruned_utc FROM snapshots WHERE id=?", (snap_id,)
            ).fetchone()[0]
        )

    def test_mark_pruned_does_not_overwrite_an_earlier_prune_time(self):
        snap_id = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        index.mark_pruned(self.conn, [snap_id], T2)
        index.mark_pruned(self.conn, [snap_id], T3)
        self.assertEqual(
            T2,
            self.conn.execute(
                "SELECT pruned_utc FROM snapshots WHERE id=?", (snap_id,)
            ).fetchone()[0],
        )

    def test_hash_is_referenced_true_while_live(self):
        self.snap(self.unit_id, self.run_id, HASH_A, T0)
        self.assertTrue(index.hash_is_referenced(self.conn, HASH_A))

    def test_hash_is_referenced_false_for_unknown_hash(self):
        self.assertFalse(index.hash_is_referenced(self.conn, HASH_B))

    def test_hash_is_referenced_false_once_all_are_pruned(self):
        snap_id = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        index.mark_pruned(self.conn, [snap_id], T3)
        self.assertFalse(index.hash_is_referenced(self.conn, HASH_A))

    def test_shared_blob_stays_referenced_until_the_last_holder_is_pruned(self):
        # Dedup means two different units can legitimately point at one blob.
        # Unlinking it when only ONE of them is pruned silently destroys the
        # other unit's only copy. This is the test that guards that.
        other_unit = index.upsert_unit(self.conn, mkunit("twin"), T0)
        mine = self.snap(self.unit_id, self.run_id, HASH_SHARED, T0)
        theirs = self.snap(other_unit, self.run_id, HASH_SHARED, T0)

        self.assertTrue(index.hash_is_referenced(self.conn, HASH_SHARED))
        index.mark_pruned(self.conn, [mine], T2)
        self.assertTrue(
            index.hash_is_referenced(self.conn, HASH_SHARED),
            "shared blob reported unreferenced while another unit still needs it",
        )
        index.mark_pruned(self.conn, [theirs], T3)
        self.assertFalse(index.hash_is_referenced(self.conn, HASH_SHARED))

    def test_same_unit_can_re_reference_a_hash_after_pruning(self):
        # A skill reverts to an older content: the new live snapshot re-refs a
        # hash whose previous snapshot was pruned.
        old = self.snap(self.unit_id, self.run_id, HASH_A, T0)
        index.mark_pruned(self.conn, [old], T1)
        self.assertFalse(index.hash_is_referenced(self.conn, HASH_A))
        self.snap(self.unit_id, self.run_id, HASH_A, T2)
        self.assertTrue(index.hash_is_referenced(self.conn, HASH_A))


class TestExportCatalog(IndexTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = index.start_run(self.conn, T0)
        self.skill_b = index.upsert_unit(self.conn, mkunit("bravo"), T0)
        self.skill_a = index.upsert_unit(self.conn, mkunit("alpha"), T0)
        self.cmd = index.upsert_unit(
            self.conn, mkunit("zulu", kind=KIND_COMMAND), T0
        )
        # Deliberately inserted out of order so sorting is doing real work.
        self.snap(self.skill_b, self.run_id, HASH_B, T2, size=20, files=2)
        self.snap(self.skill_a, self.run_id, HASH_A, T1, size=10, files=1)
        self.pruned_id = self.snap(
            self.skill_a, self.run_id, HASH_SHARED, T0, size=5, files=1
        )
        self.snap(self.cmd, self.run_id, HASH_A, T0, size=30, files=3)
        index.mark_pruned(self.conn, [self.pruned_id], T3)
        self.out = self.tmp / "catalog" / "catalog.jsonl"

    def read_lines(self):
        return [
            json.loads(line)
            for line in self.out.read_text(encoding="utf-8").splitlines()
        ]

    def test_returns_row_count_and_writes_one_line_per_snapshot(self):
        n = index.export_catalog(self.conn, self.out)
        self.assertEqual(4, n)
        self.assertEqual(4, len(self.out.read_text(encoding="utf-8").splitlines()))

    def test_creates_parent_directory(self):
        index.export_catalog(self.conn, self.out)
        self.assertTrue(self.out.exists())

    def test_sorted_by_kind_then_name_then_created(self):
        index.export_catalog(self.conn, self.out)
        rows = self.read_lines()
        self.assertEqual(
            [
                (KIND_COMMAND, "zulu", T0),
                (KIND_SKILL, "alpha", T0),
                (KIND_SKILL, "alpha", T1),
                (KIND_SKILL, "bravo", T2),
            ],
            [(r["kind"], r["name"], r["created"]) for r in rows],
        )

    def test_key_order_is_fixed(self):
        index.export_catalog(self.conn, self.out)
        first = self.out.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(
            ["kind", "name", "hash", "created", "bytes", "files", "pruned"],
            list(json.loads(first, object_pairs_hook=dict).keys()),
        )

    def test_pruned_field_carries_the_tombstone_time(self):
        index.export_catalog(self.conn, self.out)
        rows = self.read_lines()
        by_hash = {r["hash"]: r for r in rows}
        self.assertEqual(T3, by_hash[HASH_SHARED]["pruned"])
        self.assertIsNone(by_hash[HASH_B]["pruned"])

    def test_payload_fields(self):
        index.export_catalog(self.conn, self.out)
        row = [r for r in self.read_lines() if r["name"] == "bravo"][0]
        self.assertEqual(HASH_B, row["hash"])
        self.assertEqual(20, row["bytes"])
        self.assertEqual(2, row["files"])

    def test_file_ends_with_a_newline(self):
        index.export_catalog(self.conn, self.out)
        self.assertTrue(self.out.read_bytes().endswith(b"\n"))

    def test_byte_stable_across_repeated_exports(self):
        # catalog.jsonl is committed to git. Any instability here turns every
        # backup run into a spurious diff.
        index.export_catalog(self.conn, self.out)
        first = self.out.read_bytes()
        second_path = self.tmp / "catalog" / "again.jsonl"
        index.export_catalog(self.conn, second_path)
        self.assertEqual(first, second_path.read_bytes())

    def test_byte_stable_across_reconnects(self):
        index.export_catalog(self.conn, self.out)
        first = self.out.read_bytes()
        self.conn.close()
        conn2 = index.connect(self.db_path)
        self.addCleanup(conn2.close)
        other = self.tmp / "catalog" / "reconnect.jsonl"
        index.export_catalog(conn2, other)
        self.assertEqual(first, other.read_bytes())

    def test_overwrites_previous_content(self):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text("stale garbage\n" * 50, encoding="utf-8")
        index.export_catalog(self.conn, self.out)
        self.assertNotIn("stale", self.out.read_text(encoding="utf-8"))

    def test_empty_db_writes_an_empty_file(self):
        empty = index.connect(self.tmp / "empty.db")
        self.addCleanup(empty.close)
        out = self.tmp / "empty.jsonl"
        self.assertEqual(0, index.export_catalog(empty, out))
        self.assertEqual(b"", out.read_bytes())


if __name__ == "__main__":
    unittest.main()
