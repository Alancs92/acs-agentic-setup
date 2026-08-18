"""Suite B — "spec-driven".

One test per scenario in spec.md, plus one per Must-NOT. This is what the
SPEC phase alone buys you, with no gauntlet: the tests are still hand-written
example tests, but the example list came from an approved contract instead of
from the author's imagination.
"""

from datetime import datetime, timedelta, timezone

import pytest

from sessionmerge import Segment, build_timeline

UTC = timezone.utc
SYDNEY = timezone(timedelta(hours=10))


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 7, 6, hour, minute, second, tzinfo=UTC)


def seg(source: str, start: datetime, end: datetime, label: str = "x") -> Segment:
    return Segment(source=source, start=start, end=end, label=label)


# --- S1 ----------------------------------------------------------------
def test_s1_empty_input():
    assert build_timeline([]) == []


# --- S2 S3 S4 validation ----------------------------------------------
def test_s2_naive_start_rejected():
    bad = seg("meetily", datetime(2026, 7, 6, 9), at(10))
    with pytest.raises(ValueError):
        build_timeline([bad])


def test_s2_naive_end_rejected():
    bad = seg("meetily", at(9), datetime(2026, 7, 6, 10))
    with pytest.raises(ValueError):
        build_timeline([bad])


def test_s3_zero_length_rejected():
    with pytest.raises(ValueError):
        build_timeline([seg("meetily", at(9), at(9))])


def test_s3_reversed_rejected():
    with pytest.raises(ValueError):
        build_timeline([seg("meetily", at(10), at(9))])


def test_s4_unknown_source_rejected():
    with pytest.raises(ValueError):
        build_timeline([seg("zoom", at(9), at(10))])


# --- S5 normalization --------------------------------------------------
def test_s5_non_utc_input_normalized_to_utc():
    local = seg(
        "meetily",
        datetime(2026, 7, 6, 19, tzinfo=SYDNEY),
        datetime(2026, 7, 6, 20, tzinfo=SYDNEY),
    )
    (result,) = build_timeline([local])
    assert result.start == at(9)
    assert result.end == at(10)
    assert result.start.utcoffset() == timedelta(0)


# --- S6..S9 same-source merge -----------------------------------------
def test_s6_overlapping_same_source_merge_and_keep_earlier_label():
    result = build_timeline(
        [seg("meetily", at(9), at(11), "first"), seg("meetily", at(10), at(12), "second")]
    )
    assert result == [seg("meetily", at(9), at(12), "first")]


def test_s7_touching_same_source_merge():
    result = build_timeline(
        [seg("meetily", at(10), at(11), "a"), seg("meetily", at(11), at(12), "b")]
    )
    assert result == [seg("meetily", at(10), at(12), "a")]


def test_s8_one_second_gap_does_not_merge():
    result = build_timeline(
        [
            seg("meetily", at(10), at(11), "a"),
            seg("meetily", at(11, 0, 1), at(12), "b"),
        ]
    )
    assert len(result) == 2


def test_s9_chained_merge():
    result = build_timeline(
        [
            seg("meetily", at(9), at(10, 30), "a"),
            seg("meetily", at(10), at(11, 30), "b"),
            seg("meetily", at(11), at(12), "c"),
        ]
    )
    assert result == [seg("meetily", at(9), at(12), "a")]


# --- S10..S14 cross-source --------------------------------------------
def test_s10_screenpipe_mostly_covered_is_dropped():
    result = build_timeline(
        [
            seg("meetily", at(10), at(11), "meeting"),
            seg("screenpipe", at(10), at(11), "noise"),
        ]
    )
    assert [s.source for s in result] == ["meetily"]


def test_s11_coverage_exactly_at_threshold_drops():
    # screenpipe 10:00-12:00 (2h), meetily covers 10:00-11:00 (1h) => 0.5
    result = build_timeline(
        [
            seg("meetily", at(10), at(11), "meeting"),
            seg("screenpipe", at(10), at(12), "noise"),
        ]
    )
    assert [s.source for s in result] == ["meetily"]


def test_s12_ambiguous_coverage_keeps_data():
    # screenpipe 10:00-14:00 (4h), meetily covers 1h => 0.25 < 0.5 => keep
    result = build_timeline(
        [
            seg("meetily", at(10), at(11), "meeting"),
            seg("screenpipe", at(10), at(14), "coding"),
        ]
    )
    assert sorted(s.source for s in result) == ["meetily", "screenpipe"]


def test_s13_union_coverage_from_two_disjoint_meetings():
    # screenpipe 10:00-15:00 (5h); meetings 10:00-11:30 and 13:00-14:30
    # => 1.5h + 1.5h = 3h of 5h = 0.6 >= 0.5 => dropped
    result = build_timeline(
        [
            seg("meetily", at(10), at(11, 30), "m1"),
            seg("meetily", at(13), at(14, 30), "m2"),
            seg("screenpipe", at(10), at(15), "coding"),
        ]
    )
    assert [s.source for s in result] == ["meetily", "meetily"]


def test_s14_meetily_never_dropped_by_screenpipe_overlap():
    result = build_timeline(
        [
            seg("meetily", at(10), at(10, 15), "short meeting"),
            seg("screenpipe", at(9), at(17), "all day"),
        ]
    )
    assert "meetily" in [s.source for s in result]


# --- S15 ordering ------------------------------------------------------
def test_s15_deterministic_order_by_start_source_label():
    # Same start instant, both survive: the meeting is far too short to
    # cover half of the screenpipe block, so this exercises ordering only.
    result = build_timeline(
        [
            seg("screenpipe", at(9), at(11), "zzz"),
            seg("meetily", at(9), at(9, 10), "aaa"),
        ]
    )
    assert [(s.start, s.source, s.label) for s in result] == [
        (at(9), "meetily", "aaa"),
        (at(9), "screenpipe", "zzz"),
    ]


# --- Must NOTs ---------------------------------------------------------
def test_n1_input_not_mutated():
    original = [
        seg("meetily", at(11), at(12), "b"),
        seg("meetily", at(9), at(10), "a"),
    ]
    snapshot = list(original)
    build_timeline(original)
    assert original == snapshot


def test_n2_idempotent():
    segments = [
        seg("meetily", at(10), at(11), "m"),
        seg("screenpipe", at(10), at(14), "s"),
        seg("screenpipe", at(15), at(16), "s2"),
    ]
    once = build_timeline(segments)
    twice = build_timeline(once)
    assert once == twice


def test_n3_no_two_same_source_segments_overlap_or_touch():
    result = build_timeline(
        [
            seg("meetily", at(9), at(11), "a"),
            seg("meetily", at(10), at(12), "b"),
            seg("meetily", at(12), at(13), "c"),
        ]
    )
    by_source = [s for s in result if s.source == "meetily"]
    for earlier, later in zip(by_source, by_source[1:], strict=False):
        assert earlier.end < later.start


def test_n4_nothing_dropped_without_coverage():
    segments = [
        seg("screenpipe", at(9), at(10), "a"),
        seg("screenpipe", at(11), at(12), "b"),
        seg("meetily", at(14), at(15), "c"),
    ]
    assert len(build_timeline(segments)) == 3
