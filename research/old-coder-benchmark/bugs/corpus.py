"""Bug corpus — realistic defects, each derived from a spec.md clause.

Written from spec.md, NOT from the test suites, so no suite gets to be right
by construction. Each entry is a unique-string replacement into
src/sessionmerge/__init__.py.

`spec_ref` names the clause the bug violates. `kind` marks how a reviewer
would classify it: `logic`, `boundary`, `validation`, `contract`.
"""

BUGS = [
    dict(
        id="B01",
        spec_ref="S7",
        kind="boundary",
        desc="touching intervals no longer merge (<= becomes <)",
        old="if merged and segment.start <= merged[-1].end:",
        new="if merged and segment.start < merged[-1].end:",
    ),
    dict(
        id="B02",
        spec_ref="S11",
        kind="boundary",
        desc="coverage threshold exclusive instead of inclusive",
        old="if coverage >= COVERAGE_THRESHOLD:",
        new="if coverage > COVERAGE_THRESHOLD:",
    ),
    dict(
        id="B03",
        spec_ref="S12",
        kind="logic",
        desc="fail-unsafe: drops screenpipe on ANY overlap (silent data loss)",
        old="if coverage >= COVERAGE_THRESHOLD:",
        new="if coverage > 0:",
    ),
    dict(
        id="B04",
        spec_ref="S13",
        kind="logic",
        desc="coverage double-counted: union cursor never advances",
        old="            cursor = overlap_end\n",
        new="",
    ),
    dict(
        id="B05",
        spec_ref="S3",
        kind="validation",
        desc="zero-length segment passes validation (later ZeroDivisionError)",
        old="if segment.end <= segment.start:",
        new="if segment.end < segment.start:",
    ),
    dict(
        id="B06",
        spec_ref="S2",
        kind="validation",
        desc="naive-datetime check and/or swap lets naive timestamps through",
        old="if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:",
        new="if value.tzinfo is None and value.tzinfo.utcoffset(value) is None:",
    ),
    dict(
        id="B07",
        spec_ref="S5",
        kind="logic",
        desc="replace(tzinfo=utc) instead of astimezone(utc): wrong instant",
        old="        start=segment.start.astimezone(timezone.utc),\n        end=segment.end.astimezone(timezone.utc),",
        new="        start=segment.start.replace(tzinfo=timezone.utc),\n        end=segment.end.replace(tzinfo=timezone.utc),",
    ),
    dict(
        id="B08",
        spec_ref="S15",
        kind="contract",
        desc="output sorted by start only: ties fall back to insertion order",
        old="    return sorted(result, key=lambda s: (s.start, s.source, s.label))",
        new="    return sorted(result, key=lambda s: s.start)",
    ),
    dict(
        id="B09",
        spec_ref="S9",
        kind="logic",
        desc="merge takes later end unconditionally: a contained segment shrinks the span",
        old="            merged[-1] = replace(previous, end=max(previous.end, segment.end))",
        new="            merged[-1] = replace(previous, end=segment.end)",
    ),
    dict(
        id="B10",
        spec_ref="N1",
        kind="contract",
        desc="sorts the caller's list in place (input mutation)",
        old="    normalized = [_to_utc(segment) for segment in segments]",
        new="    segments.sort(key=lambda s: s.start)\n    normalized = [_to_utc(segment) for segment in segments]",
    ),
    dict(
        id="B11",
        spec_ref="S13",
        kind="boundary",
        desc="off-by-one: last coverage contributor skipped",
        old="    for other in sorted(others, key=lambda s: s.start):",
        new="    for other in sorted(others, key=lambda s: s.start)[:-1]:",
    ),
    dict(
        id="B12",
        spec_ref="S2/S5",
        kind="logic",
        desc="fast path for single-element input skips validation and normalization",
        old="    for segment in segments:\n        _validate(segment)",
        new="    if len(segments) <= 1:\n        return list(segments)\n    for segment in segments:\n        _validate(segment)",
    ),
    dict(
        id="B13",
        spec_ref="S14",
        kind="logic",
        desc="authority inverted: screenpipe treated as authoritative over meetily",
        old='AUTHORITATIVE = "meetily"',
        new='AUTHORITATIVE = "screenpipe"',
    ),
    dict(
        id="B14",
        spec_ref="S12",
        kind="logic",
        desc="overlap seconds doubled: coverage inflated, over-drops",
        old="            total += (overlap_end - overlap_start).total_seconds()",
        new="            total += (overlap_end - overlap_start).total_seconds() * 2",
    ),
    dict(
        id="B15",
        spec_ref="S2",
        kind="validation",
        desc="only `start` is checked for tz-awareness, `end` is not",
        old='    for field in ("start", "end"):',
        new='    for field in ("start",):',
    ),
    dict(
        id="B16",
        spec_ref="S6",
        kind="contract",
        desc="merged segment keeps the LATER segment's label, not the earlier",
        old="            merged[-1] = replace(previous, end=max(previous.end, segment.end))",
        new="            merged[-1] = replace(segment, start=previous.start, end=max(previous.end, segment.end))",
    ),
    dict(
        id="B17",
        spec_ref="S6/N3",
        kind="logic",
        desc="same-source merge no longer sorts first: unordered input fails to merge",
        old="    ordered = sorted(segments, key=lambda s: (s.start, s.end, s.label))",
        new="    ordered = list(segments)",
    ),
]
