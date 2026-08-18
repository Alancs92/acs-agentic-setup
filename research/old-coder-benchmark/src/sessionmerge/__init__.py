"""Merge and dedupe ambient-capture segments into a deterministic timeline.

See ../../spec.md for the behavioural contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Sequence

__all__ = ["Segment", "build_timeline", "COVERAGE_THRESHOLD", "SOURCES"]

SOURCES = ("meetily", "screenpipe")
AUTHORITATIVE = "meetily"
COVERAGE_THRESHOLD = 0.5


@dataclass(frozen=True)
class Segment:
    source: str
    start: datetime
    end: datetime
    label: str


def _validate(segment: Segment) -> None:
    if segment.source not in SOURCES:
        raise ValueError(f"unknown source: {segment.source!r}")
    for field in ("start", "end"):
        value = getattr(segment, field)
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(f"{field} must be timezone-aware")
    if segment.end <= segment.start:
        raise ValueError("end must be strictly after start")


def _to_utc(segment: Segment) -> Segment:
    return replace(
        segment,
        start=segment.start.astimezone(timezone.utc),
        end=segment.end.astimezone(timezone.utc),
    )


def _merge_same_source(segments: list[Segment]) -> list[Segment]:
    """Merge overlapping or touching intervals within one source."""
    ordered = sorted(segments, key=lambda s: (s.start, s.end, s.label))
    merged: list[Segment] = []
    for segment in ordered:
        if merged and segment.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = replace(previous, end=max(previous.end, segment.end))
        else:
            merged.append(segment)
    return merged


def _covered_seconds(target: Segment, others: list[Segment]) -> float:
    """Seconds of `target` covered by the union of `others`."""
    total = 0.0
    cursor = target.start
    for other in sorted(others, key=lambda s: s.start):
        if other.end <= cursor:
            continue
        if other.start >= target.end:
            break
        overlap_start = max(other.start, cursor)
        overlap_end = min(other.end, target.end)
        if overlap_end > overlap_start:
            total += (overlap_end - overlap_start).total_seconds()
            cursor = overlap_end
    return total


def build_timeline(segments: Sequence[Segment]) -> list[Segment]:
    for segment in segments:
        _validate(segment)

    normalized = [_to_utc(segment) for segment in segments]

    by_source: dict[str, list[Segment]] = {source: [] for source in SOURCES}
    for segment in normalized:
        by_source[segment.source].append(segment)

    authoritative = _merge_same_source(by_source[AUTHORITATIVE])
    result = list(authoritative)

    for source in SOURCES:
        if source == AUTHORITATIVE:
            continue
        for segment in _merge_same_source(by_source[source]):
            duration = (segment.end - segment.start).total_seconds()
            coverage = _covered_seconds(segment, authoritative) / duration
            if coverage >= COVERAGE_THRESHOLD:
                continue
            result.append(segment)

    return sorted(result, key=lambda s: (s.start, s.source, s.label))
