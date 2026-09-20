#!/usr/bin/env python3
"""Table-driven tests for retention.select_prunable.

This module is the last line of defence against silent data loss. Every case
below is written as (snapshots, policy, now) -> exact set of pruned ids, with
the dates worked out by hand and recorded in the comments, so that a change to
the bucketing logic fails loudly instead of quietly deleting one more snapshot
than it used to.

Two deliberate anti-tautology measures:

  * Expectations are hard-coded age lists, never re-derived from the module
    under test (a test that recomputes the answer the same way proves nothing).
  * Boundary cases are built so that the "correct" and the "off-by-one" reading
    of the rule produce *different* prune sets. A test where both readings agree
    would pass under a bug, so it would be worse than no test at all.

Run:  python3 -m unittest test_retention -v
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import unittest
from pathlib import Path

import retention
from backup_types import Snapshot

# NOTE: the shared vocabulary module is `backup_types.py`, NOT `types.py`. A
# `types.py` in this directory shadows the stdlib `types` module for any process
# whose sys.path[0] is this directory — which is every `python3 -m unittest`
# run — and CPython dies during startup. Never rename it back.

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE.parent / "config.json"

# --- reference clock --------------------------------------------------------
# 2026-08-25T03:00:00Z is a Tuesday. Every age offset in this file was resolved
# against it; the "dates" comments record the resolved calendar day, ISO week
# and calendar month so a reader can check a bucket without running code.
NOW = "2026-08-25T03:00:00Z"
_NOW_DT = dt.datetime(2026, 8, 25, 3, 0, 0, tzinfo=dt.timezone.utc)


def days_ago(n: float) -> str:
    """ISO timestamp exactly `n` days before NOW."""
    return (_NOW_DT - dt.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def snap(age_days: float, *, pruned: bool = False, unit_id: int = 7,
         sid: int | None = None, created: str | None = None) -> Snapshot:
    """Build a Snapshot whose id encodes its age, so failure output is readable.

    id == 1000 + age, so a diff of ids reads directly as a diff of ages.
    """
    if sid is None:
        sid = 1000 + int(age_days)
    return Snapshot(
        id=sid,
        unit_id=unit_id,
        content_hash="h%04d" % sid,
        blob_key="objects/aa/bb/h%04d.tar.xz" % sid,
        size_bytes=1234,
        file_count=3,
        created_utc=created if created is not None else days_ago(age_days),
        run_id=1,
        pruned_utc="2026-08-01T00:00:00Z" if pruned else None,
    )


def ids(ages) -> list[int]:
    """Expected-id helper: ages -> the ids `snap` would have given them."""
    return sorted(1000 + int(a) for a in ages)


# --- policies ---------------------------------------------------------------
# The live policy is READ FROM THE REAL config.json rather than copied, so the
# tests exercise what actually ships. It is then asserted against an inline
# copy (test_live_policy_matches_expected) — if someone tunes the config, that
# assertion fails loudly instead of every expectation below silently drifting.
LIVE_POLICY = json.loads(CONFIG_PATH.read_text())["retention"]

EXPECTED_LIVE_POLICY = {
    "always_keep_latest": 2,
    "keep_all_within_days": 30,
    "tiers": [
        {"older_than_days": 30, "thin_to": "weekly"},
        {"older_than_days": 90, "thin_to": "monthly"},
    ],
    "stable_after_days": 180,
    "stable_keep": 2,
    "never_delete_only_copy": True,
}


def policy(**overrides) -> dict:
    """Live policy with fields overridden — synthetic policies stay honest by
    starting from the shipped one."""
    p = json.loads(json.dumps(LIVE_POLICY))
    p.update(overrides)
    return p


# A policy with every protective guard switched off, used to isolate the
# banding/bucketing rules. Never use this to assert "safe" behaviour — its whole
# purpose is to remove the safety nets so the tier maths is observable.
def bare(**overrides) -> dict:
    return policy(always_keep_latest=0, never_delete_only_copy=False, **overrides)


# ---------------------------------------------------------------------------
# The table. Each entry: (name, snapshots, policy, now, expected_pruned_ids)
# ---------------------------------------------------------------------------
CASES: list[tuple] = []


def case(name, snapshots, pol, expected, now=NOW):
    CASES.append((name, snapshots, pol, now, expected))


# --- degenerate inputs ------------------------------------------------------
case("empty_input_returns_empty", [], LIVE_POLICY, [])

case("all_snapshots_already_pruned", [snap(400, pruned=True), snap(500, pruned=True)],
     LIVE_POLICY, [])

# --- last-copy floor (rule 2) ----------------------------------------------
# `never_delete_only_copy` is a FLOOR, not a count-of-one check: a unit always
# retains at least one live snapshot no matter what the rest of the policy says.
# The dangerous case is not a unit that already has one snapshot — it is a unit
# with ten that a hand-edited config takes to zero in a single run.
#
# (a) Ten snapshots, every other guard disabled, no tier covering their age:
#     rules 1 and 5 protect nothing, so without the floor all ten go. Exactly
#     one survives, and it is the NEWEST (age 1).
case("floor_keeps_exactly_one_when_all_other_rules_prune_everything",
     [snap(a) for a in range(1, 11)],
     {"keep_all_within_days": 0, "tiers": [], "always_keep_latest": 0,
      "stable_keep": 0, "never_delete_only_copy": True},
     ids(range(2, 11)))

# (b) The same floor under stability collapse. Newest is 200d old (> 180) so
#     rule 3 fires with stable_keep=0, which would take the entire history.
#     One survives: the newest (age 200).
case("floor_keeps_exactly_one_under_stability_collapse_with_stable_keep_zero",
     [snap(a) for a in [200, 210, 220, 230, 240, 250, 260, 270, 280, 290]],
     policy(always_keep_latest=0, stable_keep=0),
     ids([210, 220, 230, 240, 250, 260, 270, 280, 290]))

# (c) With the floor switched off, the same ten-snapshot unit is emptied
#     completely. This is what the floor is preventing; if a refactor drops the
#     floor, cases (a) and (b) go red while this one stays green.
case("floor_disabled_allows_a_unit_to_be_emptied_completely",
     [snap(a) for a in range(1, 11)],
     {"keep_all_within_days": 0, "tiers": [], "always_keep_latest": 0,
      "stable_keep": 0, "never_delete_only_copy": False},
     ids(range(1, 11)))

# (d) The floor is a FLOOR, not "always keep the newest". Once some other rule
#     has kept something, the floor must not fire — even if the survivor is an
#     old snapshot and the newest one is the thing being pruned.
#     keep_all_within_days=0 and a tier that starts at 100 days leaves age 5
#     matching nothing (rule 6 prunes it), while age 150 is kept by the monthly
#     tier. One live snapshot survives, so the floor is satisfied and must stay
#     out of the way. A floor that fired unconditionally would rescue age 5 too.
case("floor_does_not_fire_when_another_rule_already_kept_something",
     [snap(5), snap(150)],
     {"keep_all_within_days": 0,
      "tiers": [{"older_than_days": 100, "thin_to": "monthly"}],
      "always_keep_latest": 0, "never_delete_only_copy": True},
     ids([5]))

# A single snapshot is the unit's entire history. Pruning it is unrecoverable,
# so it must survive every other rule, including stability collapse.
case("single_recent_snapshot_never_pruned", [snap(1)], LIVE_POLICY, [])
case("single_ancient_snapshot_never_pruned", [snap(500)], LIVE_POLICY, [])

# Even with always_keep_latest disabled, the only-copy guard alone saves it.
case("single_ancient_survives_on_only_copy_guard_alone",
     [snap(500)], policy(always_keep_latest=0, stable_keep=0), [])

# Tombstoned rows do not count as "copies": one live + four tombstones is still
# a single live copy and must be kept.
case("only_copy_guard_ignores_tombstones",
     [snap(500), snap(510, pruned=True), snap(520, pruned=True),
      snap(530, pruned=True), snap(540, pruned=True)],
     policy(always_keep_latest=0, stable_keep=0), [])

# Proof the guard is load-bearing: switch it off with everything else off and
# the same lone ancient snapshot IS pruned. If a future refactor "simplifies"
# the guard away, the two cases above go red and this one stays green.
case("only_copy_guard_disabled_allows_pruning_the_last_copy",
     [snap(500)],
     policy(always_keep_latest=0, stable_keep=0, never_delete_only_copy=False),
     ids([500]))

# --- always_keep_latest overrides everything below it -----------------------
# Two ancient snapshots with stable_keep=0: collapse would take both, but
# always_keep_latest=2 outranks it.
case("two_ancient_snapshots_both_kept_by_always_keep_latest",
     [snap(400), snap(401)],
     policy(stable_keep=0, never_delete_only_copy=False),
     [])

case("always_keep_latest_exceeding_snapshot_count_keeps_all",
     [snap(300), snap(320), snap(340)],
     policy(always_keep_latest=99, stable_keep=0, never_delete_only_copy=False),
     [])

# --- stability collapse (rule 3) --------------------------------------------
# Newest is 200d old (> stable_after_days=180): the unit has settled. Ten
# snapshots spread over ten distinct-ish months collapse to the newest 2.
# Spread deliberately across months: under monthly thinning alone this set
# would keep FOUR (2026-02, 2026-01, 2025-12, 2025-11), so the case proves the
# collapse really does short-circuit rules 4-5 rather than coinciding with them.
#   200 -> 2026-02-06   210 -> 2026-01-27   220 -> 2026-01-17   230 -> 2026-01-07
#   240 -> 2025-12-28   250 -> 2025-12-18   260 -> 2025-12-08   270 -> 2025-11-28
#   280 -> 2025-11-18   290 -> 2025-11-08
_STABLE_AGES = [200, 210, 220, 230, 240, 250, 260, 270, 280, 290]
case("stability_collapse_ten_snapshots_to_stable_keep_two",
     [snap(a) for a in _STABLE_AGES],
     LIVE_POLICY,
     ids(_STABLE_AGES[2:]))  # keep 200, 210; prune the other eight

# --- keep_all_within_days (rule 4) ------------------------------------------
# Edited yesterday, 30 daily snapshots (ages 0..29), all inside the 30-day
# window: nothing may be pruned even though there are many of them.
case("thirty_daily_snapshots_all_within_keep_all_window",
     [snap(a) for a in range(0, 30)],
     LIVE_POLICY,
     [])

# --- weekly thinning (rule 5) -----------------------------------------------
# 60 daily snapshots (ages 0..59). Ages 0-29 are inside keep_all. Ages 30-59
# fall in the weekly tier and thin to one per ISO week, newest kept:
#   ISO 2026-W30 -> ages 30..36  keep 30
#   ISO 2026-W29 -> ages 37..43  keep 37
#   ISO 2026-W28 -> ages 44..50  keep 44
#   ISO 2026-W27 -> ages 51..57  keep 51
#   ISO 2026-W26 -> ages 58,59   keep 58
_WEEKLY_KEEP = {30, 37, 44, 51, 58}
_WEEKLY_PRUNE = [a for a in range(30, 60) if a not in _WEEKLY_KEEP]
case("sixty_daily_snapshots_thin_to_one_per_iso_week",
     [snap(a) for a in range(0, 60)],
     LIVE_POLICY,
     ids(_WEEKLY_PRUNE))

# --- monthly thinning past 90 days (rule 5, second tier) --------------------
# Ages 90..200 step 10, calendar months:
#   2026-05 -> 90, 100, 110    keep 90
#   2026-04 -> 120, 130, 140   keep 120
#   2026-03 -> 150, 160, 170   keep 150
#   2026-02 -> 180, 190, 200   keep 180
# BUT always_keep_latest=2 additionally rescues age 100 (2nd newest), so 100 is
# kept despite losing its bucket. Newest is 90d old, so no stability collapse.
case("monthly_thinning_past_ninety_days_with_always_keep_latest",
     [snap(a) for a in range(90, 201, 10)],
     LIVE_POLICY,
     ids([110, 130, 140, 160, 170, 190, 200]))

# Same set with always_keep_latest disabled shows the pure monthly result,
# isolating the tier maths from the top-N rescue.
case("monthly_thinning_pure_without_always_keep_latest",
     [snap(a) for a in range(90, 201, 10)],
     bare(),
     ids([100, 110, 130, 140, 160, 170, 190, 200]))

# --- full-spectrum mixed case ----------------------------------------------
#   keep_all (<30d): 0, 3, 12, 29
#   weekly  (30-89): 31 & 35 share ISO 2026-W30 -> keep 31, prune 35
#                    40 (W29), 55 (W27), 70 (W25), 88 (W22) each alone -> kept
#   monthly (>=90):  95 (2026-05), 120 (2026-04) alone -> kept
#                    150 & 175 share 2026-03 -> keep 150, prune 175
case("mixed_spectrum_across_all_three_bands",
     [snap(a) for a in [0, 3, 12, 29, 31, 35, 40, 55, 70, 88, 95, 120, 150, 175]],
     LIVE_POLICY,
     ids([35, 175]))

# --- BOUNDARY: exactly keep_all_within_days (30) ----------------------------
# Ages 29, 30, 31 -> 2026-07-27 (W31), 2026-07-26 (W30), 2026-07-25 (W30).
# Correct reading: "newer than 30 days" is age < 30, so age 29 is kept by
# keep_all and ages 30 & 31 enter the weekly tier, share ISO 2026-W30, and
# thin to the newer (30) -> prune 31.
# Off-by-one reading (age <= 30 kept by keep_all): only 31 would be in the
# tier, alone in its bucket, so NOTHING would be pruned. The two readings give
# different answers, which is exactly what makes this case worth having.
case("boundary_exactly_thirty_days_belongs_to_the_weekly_tier",
     [snap(29), snap(30), snap(31)],
     bare(),
     ids([31]))

# --- BOUNDARY: exactly the second tier threshold (90) -----------------------
# Ages 89, 90, 97 -> 2026-05-28 (W22, May), 2026-05-27 (W22, May),
# 2026-05-20 (W21, May).
# Correct reading: tier applies at age >= older_than_days and the OLDEST
# applicable tier wins, so 90 and 97 are monthly (both 2026-05 -> keep 90,
# prune 97) while 89 is weekly and alone in ISO W22.
# Off-by-one reading (monthly only past 90): 89 and 90 would share weekly W22
# -> prune 90 instead. Different answer, so the boundary is pinned.
case("boundary_exactly_ninety_days_belongs_to_the_monthly_tier",
     [snap(89), snap(90), snap(97)],
     bare(),
     ids([97]))

# Same three snapshots also prove weekly and monthly buckets never collide:
# age 89 (weekly, May) must not be swallowed by the May monthly bucket.
case("weekly_and_monthly_buckets_do_not_collide_within_one_month",
     [snap(89), snap(90)],
     bare(),
     [])

# --- BOUNDARY: exactly stable_after_days (180) ------------------------------
# Age exactly 180 is NOT "older than 180", so NO collapse.
#   180 -> 2026-02-26 (monthly 2026-02), 240 -> 2025-12-28 (monthly 2025-12)
# Distinct monthly buckets -> both kept.
case("boundary_newest_exactly_stable_after_days_does_not_collapse",
     [snap(180), snap(240)],
     bare(stable_keep=1),
     [])

# One day older and the collapse fires, keeping only stable_keep=1.
#   181 -> 2026-02-25, 241 -> 2025-12-27 (still distinct monthly buckets, so
#   without the collapse this set would also keep both — the differing answer
#   is what proves the collapse triggered).
case("boundary_newest_one_day_past_stable_after_days_collapses",
     [snap(181), snap(241)],
     bare(stable_keep=1),
     ids([241]))

# --- ISO week / year boundary ----------------------------------------------
# Evaluated at 2026-03-01, so all three land in the 30-90d weekly band.
#   2025-12-28 (Sun) -> ISO (2025, 52)   age 63
#   2025-12-29 (Mon) -> ISO (2026,  1)   age 62
#   2026-01-01 (Thu) -> ISO (2026,  1)   age 59
# 2025-12-29 and 2026-01-01 are in DIFFERENT calendar years but the SAME ISO
# week, so they must collapse to one (the newer, 2026-01-01). 2025-12-28 is one
# day earlier but a different ISO week, so it must survive separately.
# A naive (calendar_year, iso_week) key would give (2025,1) vs (2026,1) vs
# (2025,52) — three buckets, nothing pruned. A naive year-only key would merge
# the two December entries. Only true ISO year+week gives the answer below.
_ISO_NOW = "2026-03-01T00:00:00Z"
case("iso_week_spanning_new_year_buckets_by_iso_year_not_calendar_year",
     [snap(0, sid=2001, created="2025-12-28T12:00:00Z"),
      snap(0, sid=2002, created="2025-12-29T12:00:00Z"),
      snap(0, sid=2003, created="2026-01-01T12:00:00Z")],
     bare(),
     [2002],
     now=_ISO_NOW)

# Mirror image: two snapshots one day apart across a calendar-year boundary
# that ARE in different ISO weeks must both survive.
#   2025-12-27 (Sat) -> ISO (2025, 52);  2025-12-29 (Mon) -> ISO (2026, 1)
case("iso_week_adjacent_days_in_different_iso_weeks_both_kept",
     [snap(0, sid=2101, created="2025-12-27T12:00:00Z"),
      snap(0, sid=2102, created="2025-12-29T12:00:00Z")],
     bare(),
     [],
     now=_ISO_NOW)

# --- two tiers sharing a thin_to must not share buckets ---------------------
# The live config gives its two tiers different thin_to values, which masks a
# cross-tier bucket collision. Force the collision: both tiers monthly, with two
# snapshots in the SAME calendar month either side of the 90-day threshold.
#   89 -> 2026-05-28 (tier 30, month 2026-05)
#   90 -> 2026-05-27 (tier 90, month 2026-05)
# Distinct tiers means distinct buckets, so both survive. If the bucket key ever
# stops including the tier, these two collide and one is silently deleted.
case("two_tiers_with_the_same_thin_to_keep_separate_buckets",
     [snap(89), snap(90)],
     bare(tiers=[{"older_than_days": 30, "thin_to": "monthly"},
                 {"older_than_days": 90, "thin_to": "monthly"}]),
     [])

# --- identical timestamps resolve deterministically -------------------------
# created_utc is second-precision, so a fast run can record two snapshots with
# the same stamp. The survivor must be chosen by id (SQLite ids are monotonic,
# so the higher id was written later), NOT by input order — otherwise which
# snapshot lives depends on the DB's row order, which is not a guarantee.
# Both are age 100 -> same monthly bucket -> exactly one survives.
case("identical_created_utc_keeps_the_higher_id",
     [snap(100, sid=3001, created=days_ago(100)),
      snap(100, sid=3002, created=days_ago(100))],
     bare(),
     [3001])

# --- tombstones mixed with live snapshots -----------------------------------
# Tombstoned rows are invisible to every rule: they must never be returned, and
# must not occupy a bucket slot that would otherwise save a live snapshot.
case("already_pruned_rows_never_returned_and_do_not_occupy_buckets",
     [snap(29), snap(30), snap(31),
      snap(32, pruned=True), snap(33, pruned=True)],
     bare(),
     ids([31]))

# --- monthly buckets are year-scoped ----------------------------------------
# Same calendar month, different years. stable_after_days is disabled so the
# collapse does not pre-empt the tier logic.
#   290 -> 2025-11-08 (2025-11)    655 -> 2024-11-08 (2024-11)
# Distinct buckets, both kept. A month-only key would merge them and delete the
# older one — the exact failure that loses a unit's oldest surviving history.
case("monthly_buckets_are_scoped_by_year_not_month_alone",
     [snap(290), snap(655)],
     bare(stable_after_days=100000),
     [])

# --- malformed tier entries are ignored, valid ones still apply -------------
# A junk entry must neither crash nor quietly disable the real tier beside it.
#   30 -> 2026-07-26, 31 -> 2026-07-25, both ISO 2026-W30 -> keep 30, prune 31.
case("malformed_tier_entries_are_dropped_not_guessed_at",
     [snap(30), snap(31)],
     bare(tiers=[None,
                 {"thin_to": "weekly"},                       # no threshold
                 {"older_than_days": 30},                     # no thin_to
                 {"older_than_days": "nonsense", "thin_to": "weekly"},
                 {"older_than_days": 30, "thin_to": "weekly"}]),
     ids([31]))

# --- missing / empty policy: fail safe, prune nothing -----------------------
# An empty policy is a config error. The safe failure mode for a destructive
# module is to do nothing at all.
case("empty_policy_prunes_nothing", [snap(a) for a in range(0, 400, 20)], {}, [])
case("policy_without_tiers_but_with_keep_all_prunes_nothing_recent",
     [snap(a) for a in range(0, 30)],
     {"keep_all_within_days": 30, "tiers": []},
     [])

# Defaults for OMITTED keys are themselves safety features and are pinned here.
# `always_keep_latest` omitted defaults to 1, not 0: with keep_all_within_days=0
# and no tiers, every snapshot falls through to rule 6, and only that default
# saves the newest one. A 0 default would empty the unit in a single run.
case("omitted_always_keep_latest_defaults_to_one",
     [snap(5), snap(10)],
     {"keep_all_within_days": 0, "tiers": [], "never_delete_only_copy": False},
     ids([10]))

# `never_delete_only_copy` omitted defaults to True. Same construction, one
# snapshot, every other guard explicitly off: only the default stands between
# the unit and total loss.
case("omitted_never_delete_only_copy_defaults_to_true",
     [snap(5)],
     {"keep_all_within_days": 0, "tiers": [], "always_keep_latest": 0},
     [])

# A negative count must clamp to 0, not slice from the wrong end of the list.
# ordered[:-1] would "protect" everything except the oldest — wrong snapshots,
# right-looking count. With all guards off and no tiers, rule 6 takes all three.
case("negative_always_keep_latest_clamps_to_zero",
     [snap(5), snap(10), snap(15)],
     {"keep_all_within_days": 0, "tiers": [], "always_keep_latest": -1,
      "never_delete_only_copy": False},
     ids([5, 10, 15]))

# Ages are fractional days, not whole days. A backup runs at a fixed hour, so
# truncating to whole days would move a snapshot across a threshold depending on
# what time of day it was taken. Threshold 180.5 with a newest at 180.6 must
# collapse; under whole-day truncation (180) it would not.
case("age_is_fractional_days_not_truncated_to_whole_days",
     [snap(180.6, sid=4001), snap(400, sid=4002)],
     bare(stable_after_days=180.5, stable_keep=1),
     [4002])

# Clock skew: a snapshot stamped in the FUTURE (system clock jump, or a stamp
# written by another machine) has a negative age. It must sort as the newest and
# be kept, never treated as ancient and pruned.
#   -5 -> 2026-08-30, 100 -> 2026-05-17 (2026-05), 110 -> 2026-05-07 (2026-05)
#   always_keep_latest=2 keeps the future one and 100; 110 loses the May bucket.
case("future_timestamp_from_clock_skew_is_kept_not_pruned",
     [snap(-5, sid=4101), snap(100), snap(110)],
     LIVE_POLICY,
     ids([110]))

# An unrecognised thin_to must not be guessed at — keep everything in that tier.
case("unknown_thin_to_keeps_everything_in_that_tier",
     [snap(a) for a in range(30, 60)],
     bare(tiers=[{"older_than_days": 30, "thin_to": "fortnightly"}]),
     [])

# Explicit: no tier covers the age band, so rule 6 prunes. This documents that
# a truncated tiers list is genuinely destructive, which is why the empty-policy
# case above defaults keep_all_within_days to infinity instead of to 0.
case("age_beyond_keep_all_with_no_matching_tier_is_pruned",
     [snap(5), snap(40), snap(50)],
     bare(tiers=[]),
     ids([40, 50]))


class RetentionTableTests(unittest.TestCase):
    """One generated test method per row of CASES (see loop below)."""


def _make_test(name, snapshots, pol, now, expected):
    def test(self):
        got = retention.select_prunable(list(snapshots), pol, now)
        self.assertEqual(
            got, sorted(expected),
            "\ncase:     %s\nexpected: %s\ngot:      %s" % (name, sorted(expected), got),
        )
    test.__name__ = "test_" + name
    test.__doc__ = name.replace("_", " ")
    return test


for _c in CASES:
    setattr(RetentionTableTests, "test_" + _c[0], _make_test(*_c))


class RetentionInvariantTests(unittest.TestCase):
    """Properties that must hold for every case in the table, not just one."""

    def test_live_policy_matches_expected(self):
        """config.json drives the table above; changing it must break loudly."""
        self.assertEqual(LIVE_POLICY, EXPECTED_LIVE_POLICY)

    def test_input_ordering_does_not_affect_output(self):
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                forward = retention.select_prunable(list(snapshots), pol, now)
                backward = retention.select_prunable(list(reversed(snapshots)), pol, now)
                # A deterministic non-trivial shuffle: interleave from both ends.
                shuffled = []
                lo, hi = 0, len(snapshots) - 1
                while lo <= hi:
                    shuffled.append(snapshots[lo])
                    if lo != hi:
                        shuffled.append(snapshots[hi])
                    lo, hi = lo + 1, hi - 1
                self.assertEqual(forward, backward)
                self.assertEqual(forward, retention.select_prunable(shuffled, pol, now))

    def test_output_is_sorted_and_deduplicated(self):
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                got = retention.select_prunable(list(snapshots), pol, now)
                self.assertEqual(got, sorted(got))
                self.assertEqual(len(got), len(set(got)))

    def test_never_returns_an_already_pruned_id(self):
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                tombstoned = {s.id for s in snapshots if s.pruned_utc is not None}
                got = set(retention.select_prunable(list(snapshots), pol, now))
                self.assertEqual(got & tombstoned, set())

    def test_last_copy_floor_holds_across_every_case(self):
        """The floor as a global invariant, not just in its own cases.

        For any policy that leaves `never_delete_only_copy` on, no case in the
        table may ever prune a unit's entire live history. This is the assertion
        that would catch a floor bypass introduced by some *future* rule, since
        it applies to every row automatically.
        """
        for name, snapshots, pol, now, expected in CASES:
            if not pol.get("never_delete_only_copy", True):
                continue  # floor explicitly disabled for this case
            live = {s.id for s in snapshots if s.pruned_utc is None}
            if not live:
                continue
            with self.subTest(case=name):
                got = set(retention.select_prunable(list(snapshots), pol, now))
                self.assertTrue(
                    live - got,
                    "case %s pruned every live snapshot despite the floor" % name)

    def test_output_ids_are_always_a_subset_of_input_ids(self):
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                got = set(retention.select_prunable(list(snapshots), pol, now))
                self.assertTrue(got <= {s.id for s in snapshots})

    def test_input_list_is_not_mutated(self):
        """Callers pass DB query results; the engine must not reorder them."""
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                given = list(snapshots)
                before = list(given)
                retention.select_prunable(given, pol, now)
                self.assertEqual(given, before)

    def test_repeated_calls_are_identical(self):
        """No hidden clock, no RNG, no accumulated state between calls."""
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                a = retention.select_prunable(list(snapshots), pol, now)
                b = retention.select_prunable(list(snapshots), pol, now)
                self.assertEqual(a, b)

    def test_policy_dict_is_not_mutated(self):
        for name, snapshots, pol, now, expected in CASES:
            with self.subTest(case=name):
                before = json.dumps(pol, sort_keys=True)
                retention.select_prunable(list(snapshots), pol, now)
                self.assertEqual(json.dumps(pol, sort_keys=True), before)


class RetentionPurityTests(unittest.TestCase):
    """Mechanically enforce the purity contract, so a future edit cannot quietly
    reach for the filesystem, the DB or the wall clock."""

    ALLOWED_IMPORTS = {"datetime", "typing", "backup_types", "__future__"}

    def _module_source(self) -> str:
        return (HERE / "retention.py").read_text()

    def test_imports_are_limited_to_datetime_and_shared_types(self):
        tree = ast.parse(self._module_source())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import
                    continue
                imported.add((node.module or "").split(".")[0])
        self.assertTrue(
            imported <= self.ALLOWED_IMPORTS,
            "retention.py must not import %s" % sorted(imported - self.ALLOWED_IMPORTS),
        )

    def test_no_clock_or_io_calls_in_source(self):
        src = self._module_source()
        # Substring check on purpose: it catches an attribute call even if the
        # module was imported under an alias.
        for forbidden in ("utcnow_iso", "datetime.now", "time.time", "open(",
                          "Path(", "os.", "sqlite3", "random."):
            self.assertNotIn(
                forbidden, src,
                "retention.py must stay pure — found %r" % forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
