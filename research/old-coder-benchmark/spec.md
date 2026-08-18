# SPEC — `sessionmerge.build_timeline`

Written **before** the implementation, the test suites, and the bug corpus.
Everything downstream is derived from this document, not from each other.

Domain chosen deliberately: `sediment/docs/standards/testing.md` names
"dedupe logic (Screenpipe vs. Meetily overlap) — silent data loss or
duplication here is the most likely real bug" as risk #1 in the project.

## Data model

```
Segment(source: str, start: datetime, end: datetime, label: str)
```

- `source` ∈ {`"meetily"`, `"screenpipe"`}
- `start`, `end` are timezone-aware datetimes
- interval semantics are half-open: `[start, end)`

## API

`build_timeline(segments: Sequence[Segment]) -> list[Segment]`

## Scenarios

S1  empty input → `[]`

S2  validation: a naive (tzinfo-less) datetime → `ValueError`
S3  validation: `end <= start` (incl. zero-length) → `ValueError`
S4  validation: unknown `source` → `ValueError`

S5  normalization: input in a non-UTC zone comes back expressed in UTC,
    representing the same instant

S6  same-source merge: two overlapping meetily segments become one spanning
    `min(start)..max(end)`, taking the earlier segment's label

S7  same-source touching merge: `[10:00,11:00)` and `[11:00,12:00)` from the
    same source merge into `[10:00,12:00)` (half-open intervals that abut are
    contiguous coverage)

S8  same-source non-touching: a one-second gap does NOT merge

S9  chained merge: A overlaps B, B overlaps C, A does not overlap C →
    all three become one segment

S10 cross-source drop: a screenpipe segment ≥ 50% covered by meetily
    segments is dropped (meetily is authoritative for meetings)

S11 cross-source threshold boundary: coverage of exactly 0.5 drops
    (`>=`, not `>`)

S12 cross-source fail-safe: coverage strictly between 0 and 0.5 KEEPS the
    screenpipe segment. On ambiguous overlap, prefer keeping data over
    silently dropping it (`sediment/docs/standards/code-review.md`).

S13 cross-source union coverage: two *disjoint* meetily segments each
    covering 30% of one screenpipe segment sum to 60% → dropped.
    Coverage is measured over the union, never double-counted.

S14 meetily is never dropped by screenpipe overlap (asymmetric rule)

S15 deterministic order: output sorted by `(start, source, label)`

## Must NOT

N1  must not mutate the input sequence or any input Segment
N2  must not be non-idempotent: `build_timeline(build_timeline(x))`
    must equal `build_timeline(x)`
N3  must not emit two segments of the same source that overlap or touch
N4  must not silently drop a segment for any reason other than rule S10
