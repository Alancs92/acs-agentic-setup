"""Suite A — "ad-hoc TDD".

What gets written when the rule is only "TDD is not optional" (sediment's
docs/standards/testing.md) and there is no written spec: happy paths, the
obvious edge cases, one validation check. Written from the informal request
("merge and dedupe capture segments, meetily wins"), NOT from spec.md.
"""

from datetime import datetime, timedelta, timezone

import pytest

from sessionmerge import Segment, build_timeline

UTC = timezone.utc


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 6, hour, minute, tzinfo=UTC)


def seg(source: str, start_h: int, end_h: int, label: str = "x") -> Segment:
    return Segment(source=source, start=at(start_h), end=at(end_h), label=label)


def test_empty_input_returns_empty():
    assert build_timeline([]) == []


def test_overlapping_meetily_segments_merge():
    result = build_timeline([seg("meetily", 9, 11, "a"), seg("meetily", 10, 12, "b")])
    assert len(result) == 1
    assert result[0].start == at(9)
    assert result[0].end == at(12)


def test_screenpipe_inside_meeting_is_dropped():
    result = build_timeline(
        [seg("meetily", 9, 12, "standup"), seg("screenpipe", 10, 11, "noise")]
    )
    assert [s.source for s in result] == ["meetily"]


def test_screenpipe_with_no_overlap_is_kept():
    result = build_timeline(
        [seg("meetily", 9, 10, "standup"), seg("screenpipe", 14, 15, "coding")]
    )
    assert len(result) == 2


def test_output_is_sorted_by_start():
    result = build_timeline(
        [seg("screenpipe", 14, 15, "b"), seg("screenpipe", 9, 10, "a")]
    )
    assert [s.start for s in result] == [at(9), at(14)]


def test_naive_datetime_is_rejected():
    naive = Segment(
        source="meetily",
        start=datetime(2026, 7, 6, 9),
        end=datetime(2026, 7, 6, 10),
        label="a",
    )
    with pytest.raises(ValueError):
        build_timeline([naive])


def test_unknown_source_is_rejected():
    bad = Segment(
        source="zoom", start=at(9), end=at(10), label="a"
    )
    with pytest.raises(ValueError):
        build_timeline([bad])


def test_long_screenpipe_block_around_short_meeting_is_kept():
    # A 4h coding block containing a 30min meeting is mostly not the meeting.
    result = build_timeline(
        [
            Segment("meetily", at(10), at(10, 30), "standup"),
            Segment("screenpipe", at(9), at(13), "coding"),
        ]
    )
    assert len(result) == 2
    assert timedelta(0) < result[0].end - result[0].start
