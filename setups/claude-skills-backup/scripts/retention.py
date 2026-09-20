#!/usr/bin/env python3
"""Retention policy engine for claude-skills-backup — PURE, no side effects.

Purpose
    Decide which of one unit's snapshots may be pruned. This module makes the
    only irreversible decision in the pipeline: everything it returns will have
    its blob unlinked. It therefore does nothing else — no filesystem, no DB, no
    clock, no logging — so that it is exhaustively testable in isolation.

Inputs
    snapshots  list[Snapshot]  one unit's snapshots, in ANY order. Rows whose
                               pruned_utc is set are tombstones: already pruned,
                               invisible to every rule, never returned again.
    policy     dict            the `retention` block of config.json.
    now        str             UTC ISO-8601 'YYYY-MM-DDTHH:MM:SSZ'. Passed in,
                               never read from the system clock, so behaviour is
                               deterministic under test.

Output
    list[int]  snapshot ids to prune, sorted ascending, deduplicated, always a
               subset of the live input ids.

Design notes for whoever edits this next
    * Every guard here exists because removing it destroys user data in some
      real configuration. In particular the last-copy floor (rule 2, in
      `_finish`) looks redundant next to `always_keep_latest` and is NOT — it is
      the backstop against a hand-edited config, and it is the reason `_finish`
      is the single exit point. See its docstring.
    * The module keeps a KEEP set and prunes the complement, rather than
      building a PRUNE set directly. Under a keep-set the failure mode of a bug
      is "kept too much" (wastes disk); under a prune-set it is "deleted too
      much" (unrecoverable). Do not invert this.
    * Missing policy keys fail SAFE: an absent threshold is treated as infinite
      (keep everything) rather than as zero (prune everything), because a
      truncated or malformed config must not silently wipe history.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional, Sequence, Tuple

from backup_types import Snapshot, parse_iso

__all__ = ["select_prunable"]

# Sentinel for "no threshold configured" — see the fail-safe note above. Using
# infinity rather than None keeps the comparisons uniform and unmistakable.
_INFINITE_DAYS = float("inf")

_SECONDS_PER_DAY = 86400.0

# Recognised `thin_to` values. An unrecognised value is NOT guessed at: every
# snapshot in that tier is kept. Deleting data on the strength of a typo in
# config.json is not an acceptable failure mode.
_THIN_WEEKLY = "weekly"
_THIN_MONTHLY = "monthly"


def select_prunable(snapshots: List[Snapshot], policy: dict,
                    now: str) -> List[int]:
    """Given one unit's snapshots (any order) return snapshot ids to prune.

    Pure. No filesystem, no DB, no clock — `now` is passed in.
    Already-pruned snapshots are ignored (never returned twice).

    Rules, applied in this order:
      1. The newest `always_keep_latest` are ALWAYS kept.
      2. If `never_delete_only_copy`, the unit ALWAYS retains at least one live
         snapshot — a hard floor, not a count-of-one check. If every other rule
         would prune everything (`always_keep_latest: 0` with `stable_keep: 0`,
         or a tier list that covers nothing), the newest survivor is kept
         anyway. See `_finish` for why this cannot be bypassed.
      3. Stability collapse: if the NEWEST snapshot is older than
         `stable_after_days`, the unit has settled — keep only the newest
         `stable_keep`, prune the rest. Skip remaining rules.
      4. Snapshots newer than `keep_all_within_days` are all kept.
      5. For each tier, oldest-applicable wins: within the tier's age band keep
         one snapshot per bucket (`weekly` = ISO year+week of created_utc,
         `monthly` = year+month), keeping the NEWEST in each bucket.
      6. Anything not kept by the above is pruned.
    """
    # --- rule 0 (implicit): tombstones are not candidates -------------------
    # A tombstoned row's blob is already gone; returning its id again would make
    # the caller try to unlink a missing blob and, worse, would double-count
    # "blobs_pruned" in the run stats. History rows stay, they just stop
    # participating in every rule below — including the only-copy count, so a
    # unit with one live snapshot and fifty tombstones still has exactly one
    # copy worth protecting.
    live = [s for s in snapshots if s.pruned_utc is None]
    if not live:
        return []

    now_dt = parse_iso(now)

    # Newest first. The id is the tie-breaker so that two snapshots recorded in
    # the same second produce a stable, order-independent answer — without it,
    # the caller's row order would leak into which snapshot survives.
    ordered = sorted(live, key=_sort_key, reverse=True)

    keep = set()  # ids protected by at least one rule

    # --- rule 1: the newest N are always kept -------------------------------
    # Unconditional and evaluated first, so it outranks stability collapse and
    # every tier below. This is the guarantee that a restore always has recent
    # material to work from.
    always_keep_latest = _non_negative_int(policy.get("always_keep_latest"), default=1)
    keep.update(s.id for s in ordered[:always_keep_latest])

    # --- rule 2: the last-copy floor ----------------------------------------
    # Rule 2 is NOT evaluated here. It is a floor, not a filter: it has to apply
    # to whatever the rules below finally decide, so it lives in `_finish`,
    # through which every return path is funnelled. Enforcing it here instead
    # (as a "len(ordered) == 1" test) would only catch units that already had a
    # single snapshot, and would miss the far worse case — ten snapshots taken
    # down to zero in one run by `always_keep_latest: 0` plus `stable_keep: 0`.
    # See `_finish` for the guarantee and the reasoning.

    # --- rule 3: stability collapse -----------------------------------------
    # If even the NEWEST snapshot is old, the unit has stopped changing. Keeping
    # a per-week/per-month spread of a skill that has not been touched in six
    # months is pure waste, so collapse to the newest `stable_keep` and stop.
    # Note the strict `>`: at exactly `stable_after_days` the unit is not yet
    # "older than" the threshold, so it does not collapse. Tested explicitly.
    stable_after_days = _days_threshold(policy.get("stable_after_days"))
    newest_age = _age_days(now_dt, ordered[0])
    if newest_age > stable_after_days:
        stable_keep = _non_negative_int(policy.get("stable_keep"), default=1)
        keep.update(s.id for s in ordered[:stable_keep])
        # "Skip remaining rules" — the collapse is the whole answer for a
        # settled unit; running the tiers afterwards would resurrect snapshots
        # the collapse just decided to drop.
        return _finish(ordered, keep, policy)

    # --- rule 4: recent snapshots are all kept ------------------------------
    # Strict `<`: "newer than keep_all_within_days" means age < threshold.
    # A snapshot at EXACTLY the threshold is not newer than it, so it falls
    # through to the tiers below. That handoff has to be exact and exhaustive —
    # if rule 4 used `<` and the tiers used `>`, a snapshot sitting precisely on
    # the boundary would match neither and be silently pruned by rule 6.
    keep_all_within_days = _days_threshold(policy.get("keep_all_within_days"))

    tiers = _normalised_tiers(policy.get("tiers"))

    # --- rule 5: tiered thinning --------------------------------------------
    # buckets maps a bucket key -> id of the newest snapshot seen for it. Since
    # `ordered` is newest-first, the FIRST snapshot to claim a bucket is the
    # newest one in it, which is the one the spec says to keep.
    buckets = {}  # type: Dict[Tuple, int]

    for snapshot in ordered:
        age = _age_days(now_dt, snapshot)

        if age < keep_all_within_days:
            keep.add(snapshot.id)
            continue

        tier = _applicable_tier(tiers, age)
        if tier is None:
            # Rule 6: past the keep-all window with no tier covering this age.
            # Nothing keeps it. This is reachable with an empty `tiers` list,
            # which is why an ABSENT keep_all_within_days is treated as infinite
            # rather than as 0 — a half-written config should keep everything,
            # not prune everything.
            continue

        threshold, thin_to = tier
        bucket = _bucket_key(snapshot, thin_to)
        if bucket is None:
            # Unrecognised `thin_to`: keep the snapshot rather than invent a
            # bucketing scheme. A config typo must cost disk, never data.
            keep.add(snapshot.id)
            continue

        # The tier threshold is part of the key so two tiers can never share a
        # bucket. Without it, a `weekly` tier and a `monthly` tier that both
        # resolved to the same (year, number) pair would collide and one band
        # would eat the other's survivor.
        full_key = (threshold, thin_to) + bucket
        if full_key not in buckets:
            buckets[full_key] = snapshot.id
            keep.add(snapshot.id)

    # --- rule 6: everything not kept above is pruned ------------------------
    return _finish(ordered, keep, policy)


# --- helpers ---------------------------------------------------------------

def _sort_key(snapshot: Snapshot) -> Tuple[_dt.datetime, int]:
    """Total order over snapshots: creation time, then id as tie-breaker.

    The tie-breaker is what makes the result independent of input ordering when
    two snapshots share a created_utc (second precision makes that plausible
    during a fast run).
    """
    return (parse_iso(snapshot.created_utc), snapshot.id)


def _age_days(now_dt: _dt.datetime, snapshot: Snapshot) -> float:
    """Age in fractional days. Fractional, not integer: rounding to whole days
    would move snapshots across tier boundaries depending on the time of day the
    backup happened to run."""
    delta = now_dt - parse_iso(snapshot.created_utc)
    return delta.total_seconds() / _SECONDS_PER_DAY


def _days_threshold(value) -> float:
    """A day threshold from config, defaulting to infinity when absent.

    Infinity is the safe default in both places this is used:
      * keep_all_within_days absent -> every snapshot is "recent" -> keep all.
      * stable_after_days absent    -> nothing is ever "settled" -> no collapse.
    """
    if value is None:
        return _INFINITE_DAYS
    try:
        return float(value)
    except (TypeError, ValueError):
        # Unparseable threshold is a config error; fail safe, keep everything.
        return _INFINITE_DAYS


def _non_negative_int(value, default: int) -> int:
    """Count from config, clamped at 0. A negative count would slice from the
    wrong end of the list and protect nothing."""
    if value is None:
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _normalised_tiers(raw) -> List[Tuple[float, str]]:
    """Config tiers -> [(older_than_days, thin_to)], sorted oldest threshold
    first so `_applicable_tier` can take the first match.

    Malformed entries are dropped rather than guessed at: a tier we cannot read
    must not be allowed to thin anything.
    """
    tiers = []  # type: List[Tuple[float, str]]
    if not raw:
        return tiers
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        threshold = entry.get("older_than_days")
        thin_to = entry.get("thin_to")
        if threshold is None or not isinstance(thin_to, str):
            continue
        try:
            tiers.append((float(threshold), thin_to))
        except (TypeError, ValueError):
            continue
    # Descending threshold: "oldest-applicable wins" per the contract, so a
    # 200-day-old snapshot lands in the 90-day monthly tier, not the 30-day
    # weekly one it also technically qualifies for.
    tiers.sort(key=lambda t: t[0], reverse=True)
    return tiers


def _applicable_tier(tiers: List[Tuple[float, str]],
                     age: float) -> Optional[Tuple[float, str]]:
    """The oldest tier whose threshold this age reaches, or None.

    `>=`, not `>`: a snapshot at exactly `older_than_days` belongs to the tier.
    Paired with the strict `<` in rule 4 this partitions the age line with no
    gap — and a gap here means silent deletion, not merely odd behaviour.
    """
    for threshold, thin_to in tiers:  # already sorted oldest-first
        if age >= threshold:
            return (threshold, thin_to)
    return None


def _bucket_key(snapshot: Snapshot, thin_to: str) -> Optional[Tuple]:
    """Bucket a snapshot for its tier, or None if `thin_to` is unrecognised."""
    created = parse_iso(snapshot.created_utc)
    if thin_to == _THIN_WEEKLY:
        # ISO year+week, NOT calendar year + ISO week. 2025-12-29 is ISO week 1
        # of ISO YEAR 2026 while its calendar year is 2025; pairing the calendar
        # year with the ISO week would put it in a phantom "2025 week 1" bucket,
        # splitting a single ISO week across two buckets (keeps too much) and,
        # in the mirror case, merging two distinct weeks (deletes too much).
        iso = created.isocalendar()
        return (_THIN_WEEKLY, iso[0], iso[1])
    if thin_to == _THIN_MONTHLY:
        return (_THIN_MONTHLY, created.year, created.month)
    return None


def _finish(ordered: Sequence[Snapshot], keep, policy: dict) -> List[int]:
    """Apply the last-copy floor, then return the ids nobody kept.

    THE ONLY EXIT from select_prunable once a live snapshot exists. Every rule
    path returns through here specifically so the floor cannot be bypassed by
    adding a new early return later — if you add a rule, return through _finish.

    The floor (rule 2, `never_delete_only_copy`): a unit must always retain at
    least one live snapshot, whatever the rest of the policy says. This is
    deliberately stronger than "if only one copy exists, keep it" — the
    dangerous case is not a unit that already has one snapshot, it is a unit
    with ten that a hand-edited config (`always_keep_latest: 0` together with
    `stable_keep: 0`, or a `tiers` list covering nothing) would take to zero in
    a single run. These blobs are frequently the only copy of a skill that
    exists anywhere; the cost of the floor is one snapshot per unit, the cost of
    omitting it is unbounded and silent, since the unit still shows up in the
    catalog as a row of tombstones.

    The survivor is the NEWEST snapshot: the same one every other rule treats as
    most valuable, and the only choice that makes `restore` do something useful.
    """
    keep = set(keep)
    if policy.get("never_delete_only_copy", True) and not keep and ordered:
        keep.add(ordered[0].id)  # `ordered` is newest-first

    # Sorted and deduplicated so the caller's DB update and its run stats are
    # deterministic regardless of input order.
    return sorted({s.id for s in ordered} - keep)
