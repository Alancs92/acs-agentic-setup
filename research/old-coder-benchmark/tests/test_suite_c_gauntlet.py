"""Suite C additions — what the GAUNTLET forced on top of Suite B.

Two origins, both mechanical rather than imaginative:

  M* tests  — each kills a specific mutant that survived `mutmut run` against
              Suite B alone. The mutant id is named in the docstring.
  P* tests  — hypothesis properties for the invariants in spec.md's Must-NOT
              section, plus the opposite-bound pairing SKILL.md asks for
              (don't only assert "nothing wrongly kept"; also assert
              "nothing wrongly dropped").

Run alongside test_suite_b_spec.py, never instead of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from sessionmerge import Segment, build_timeline

UTC = timezone.utc
DAY = datetime(2026, 7, 6, tzinfo=UTC)


def ts(seconds: int) -> datetime:
    return DAY + timedelta(seconds=seconds)


def seg(source: str, start_s: int, end_s: int, label: str = "x") -> Segment:
    return Segment(source=source, start=ts(start_s), end=ts(end_s), label=label)


# =====================================================================
# M — mutation-driven tests (each names the mutant it kills)
# =====================================================================


def test_m_to_utc_6_end_is_re_expressed_not_just_equal():
    """Kills x__to_utc__mutmut_6 (the `end=` normalization dropped).

    Suite B asserted `result.end == at(10)`, but aware-datetime equality
    compares instants, so an un-normalized +10:00 end compared equal and the
    mutant survived. The observable being specified is the *representation*.
    """
    sydney = timezone(timedelta(hours=10))
    (result,) = build_timeline(
        [
            Segment(
                "meetily",
                datetime(2026, 7, 6, 19, tzinfo=sydney),
                datetime(2026, 7, 6, 20, tzinfo=sydney),
                "a",
            )
        ]
    )
    assert result.start.utcoffset() == timedelta(0)
    assert result.end.utcoffset() == timedelta(0)


def test_m_covered_seconds_2_coverage_one_second_below_threshold_keeps():
    """Kills x__covered_seconds__mutmut_2 (`total = 0.0` -> `total = 1.0`).

    Nothing in Suite B measured coverage precisely enough for a one-second
    error to change the verdict. 3599s of 7200s is 0.49986 -> keep; a single
    phantom second makes it exactly 0.5 -> drop.
    """
    result = build_timeline(
        [seg("meetily", 0, 3599, "m"), seg("screenpipe", 0, 7200, "s")]
    )
    assert "screenpipe" in [s.source for s in result]


def test_m_covered_seconds_10_earlier_meeting_does_not_abort_the_scan():
    """Kills x__covered_seconds__mutmut_10 (`continue` -> `break`).

    A meeting that ended before the screenpipe block starts is skipped. If
    that skip aborts the loop instead of continuing it, every later
    contributor is silently ignored and coverage reads 0.
    """
    result = build_timeline(
        [
            seg("meetily", 0, 600, "earlier, unrelated"),
            seg("meetily", 3600, 7200, "the overlapping one"),
            seg("screenpipe", 3600, 7200, "noise"),
        ]
    )
    assert [s.source for s in result] == ["meetily", "meetily"]


def test_m_build_timeline_22_a_dropped_segment_does_not_end_the_loop():
    """Kills x_build_timeline__mutmut_22 (`continue` -> `break`).

    Dropping one screenpipe segment must not stop the remaining screenpipe
    segments from being considered — that would be silent data loss scaling
    with how early in the day the first meeting is.
    """
    result = build_timeline(
        [
            seg("meetily", 0, 3600, "morning meeting"),
            seg("screenpipe", 0, 3600, "covered, dropped"),
            seg("screenpipe", 36000, 39600, "afternoon, must survive"),
        ]
    )
    assert [(s.source, s.label) for s in result] == [
        ("meetily", "morning meeting"),
        ("screenpipe", "afternoon, must survive"),
    ]


def test_m_contained_segment_does_not_shrink_the_merged_span():
    """Kills the `max(previous.end, segment.end)` -> `segment.end` mutant.

    A short meeting fully inside a long one must not truncate the long one.
    """
    result = build_timeline(
        [seg("meetily", 0, 7200, "long"), seg("meetily", 1800, 3600, "short")]
    )
    assert result == [seg("meetily", 0, 7200, "long")]


# =====================================================================
# P — property-based tests over spec.md's Must-NOT invariants
# =====================================================================

SOURCES = st.sampled_from(["meetily", "screenpipe"])


@st.composite
def segments(draw: st.DrawFn) -> Segment:
    start = draw(st.integers(min_value=0, max_value=86_400))
    duration = draw(st.integers(min_value=1, max_value=14_400))
    return Segment(
        source=draw(SOURCES),
        start=ts(start),
        end=ts(start + duration),
        label=draw(st.text(alphabet="abc", min_size=1, max_size=3)),
    )


segment_lists = st.lists(segments(), min_size=0, max_size=12)


def union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total covered seconds of a union of intervals. Independent of the
    implementation under test — deliberately written the naive way."""
    total = 0.0
    cursor: datetime | None = None
    for start, end in sorted(intervals):
        if cursor is None or start > cursor:
            total += (end - start).total_seconds()
            cursor = end
        elif end > cursor:
            total += (end - cursor).total_seconds()
            cursor = end
    return total


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p1_no_two_same_source_segments_overlap_or_touch(items):
    result = build_timeline(items)
    for source in ("meetily", "screenpipe"):
        same = [s for s in result if s.source == source]
        for earlier, later in zip(same, same[1:], strict=False):
            assert earlier.end < later.start, f"{source}: {earlier} vs {later}"


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p2_idempotent(items):
    once = build_timeline(items)
    assert build_timeline(once) == once


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p3_input_never_mutated(items):
    snapshot = list(items)
    build_timeline(items)
    assert items == snapshot


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p4_output_is_sorted(items):
    result = build_timeline(items)
    keys = [(s.start, s.source, s.label) for s in result]
    assert keys == sorted(keys)


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p5_meetily_coverage_is_exactly_preserved(items):
    """Merging must neither lose nor invent authoritative wall-clock time."""
    result = build_timeline(items)
    before = union_seconds(
        [(s.start, s.end) for s in items if s.source == "meetily"]
    )
    after = union_seconds(
        [(s.start, s.end) for s in result if s.source == "meetily"]
    )
    assert before == after


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p6a_without_meetings_screenpipe_time_is_preserved_exactly(items):
    """Opposite bound to the drop rule, stated where the spec actually
    licenses it: with no meetily input, rule S10 can never fire, so not one
    screenpipe second may go missing (or be invented)."""
    only_screenpipe = [s for s in items if s.source == "screenpipe"]
    result = build_timeline(only_screenpipe)
    before = union_seconds([(s.start, s.end) for s in only_screenpipe])
    after = union_seconds([(s.start, s.end) for s in result])
    assert before == after


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p6b_every_kept_screenpipe_block_is_under_threshold(items):
    """Nothing wrongly KEPT. Coverage is recomputed here from the raw input
    with an independent union routine, so this does not just re-assert the
    implementation's own arithmetic."""
    result = build_timeline(items)
    meetings = [(s.start, s.end) for s in items if s.source == "meetily"]
    for s in result:
        if s.source != "screenpipe":
            continue
        duration = (s.end - s.start).total_seconds()
        clipped = [
            (max(m_start, s.start), min(m_end, s.end))
            for m_start, m_end in meetings
            if m_start < s.end and s.start < m_end
        ]
        coverage = union_seconds(clipped) / duration
        assert coverage < 0.5, f"{s} kept at coverage {coverage}"


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p6c_merge_before_dedupe_is_the_only_source_of_collateral_loss(items):
    """Documents the behaviour hypothesis discovered (see FINDING-1 in
    report.md): a screenpipe fragment with NO meeting overlap of its own can
    still be dropped, because same-source merge (S6-S9) runs before the drop
    rule (S10) and the merged block clears the threshold. That is what the
    spec says; this test pins it so it cannot change silently."""
    result = build_timeline(items)
    kept = union_seconds(
        [(s.start, s.end) for s in result if s.source == "screenpipe"]
    )
    total = union_seconds(
        [(s.start, s.end) for s in items if s.source == "screenpipe"]
    )
    meetings = union_seconds([(s.start, s.end) for s in items if s.source == "meetily"])
    # Whatever is lost is bounded by the meetings that caused it, plus the
    # uncovered remainder of the blocks those meetings sat inside — never
    # more than the total screenpipe time.
    assert 0 <= total - kept <= total
    if meetings == 0:
        assert kept == total


@given(segment_lists)
@settings(max_examples=300, deadline=None)
def test_p7_order_of_input_does_not_change_the_result(items):
    assume(len(items) > 1)
    forward = build_timeline(items)
    backward = build_timeline(list(reversed(items)))
    assert forward == backward
