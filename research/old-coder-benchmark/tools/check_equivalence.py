"""Differential check: is an escaped bug a real defect or an equivalent mutant?

Runs the original and the mutated implementation over a large randomized
corpus and reports the first input where their outputs differ. "No difference
in N random inputs" is evidence of equivalence, not proof — reported as such.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "sessionmerge" / "__init__.py"
sys.path.insert(0, str(ROOT / "bugs"))
from corpus import BUGS  # noqa: E402

UTC = timezone.utc
DAY = datetime(2026, 7, 6, tzinfo=UTC)
N = 20_000


def load(source: str, name: str):
    path = ROOT / f"_variant_{name}.py"
    path.write_text(source)
    modname = f"variant_{name}"
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolves annotations via sys.modules[cls.__module__].
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    path.unlink()
    return module


ZONES = [timezone.utc, timezone(timedelta(hours=10)), timezone(timedelta(hours=-5)), None]


def corpus(seed: int):
    """Well-formed inputs AND the malformed ones validation is supposed to
    reject. A generator that only emits valid UTC data cannot distinguish a
    broken validator from a working one, and would report every validation
    bug as "equivalent"."""
    rng = random.Random(seed)
    for _ in range(N):
        n = rng.randint(0, 6)
        items = []
        for _ in range(n):
            # Coarse grid so exact-threshold coverage (0.5) actually occurs.
            start = rng.randrange(0, 40_000, 900)
            duration = rng.choice([0, 900, 1800, 3600, 7200, rng.randint(1, 20_000)])
            items.append(
                (
                    rng.choices(["meetily", "screenpipe", "zoom"], weights=[48, 48, 4])[0],
                    start,
                    start + duration,
                    rng.choice(["a", "b", "c"]),
                    # Drawn per FIELD, not per segment: a shared zone can never
                    # produce an aware start beside a naive end, so a validator
                    # that only checks `start` would read as equivalent.
                    rng.choices(ZONES, weights=[70, 12, 12, 6])[0],
                    rng.choices(ZONES, weights=[70, 12, 12, 6])[0],
                )
            )
        yield items


def build(module, raw):
    out = []
    for source, a, b, label, start_zone, end_zone in raw:
        start_base = DAY.replace(tzinfo=start_zone)
        end_base = DAY.replace(tzinfo=end_zone)
        out.append(
            module.Segment(
                source=source,
                start=start_base + timedelta(seconds=a),
                end=end_base + timedelta(seconds=b),
                label=label,
            )
        )
    return out


def as_key(segments):
    # utcoffset is part of the observable: two aware datetimes for the same
    # instant compare equal, so representation must be captured separately or
    # normalization bugs read as equivalent.
    return [
        (s.source, s.start, s.start.utcoffset(), s.end, s.end.utcoffset(), s.label)
        for s in segments
    ]


def outcome(module, raw):
    """Observable behaviour: the returned timeline, a raised exception type,
    or a mutation of the caller's list — all three are part of the contract."""
    segments = build(module, raw)
    caller_list = list(segments)
    snapshot = list(caller_list)
    try:
        result = ("ok", as_key(module.build_timeline(caller_list)))
    except Exception as exc:
        result = ("raised", type(exc).__name__)
    return (result, caller_list == snapshot)


def main() -> int:
    original_source = TARGET.read_text()
    base = load(original_source, "base")

    wanted = sys.argv[1:] or None
    exit_code = 0

    for bug in BUGS:
        if wanted and bug["id"] not in wanted:
            continue
        mutant_source = original_source.replace(bug["old"], bug["new"])
        assert mutant_source != original_source, bug["id"]
        mutant = load(mutant_source, bug["id"])

        differing = None
        for raw in corpus(seed=abs(hash(bug["id"])) & 0xFFFF):
            left = outcome(base, raw)
            right = outcome(mutant, raw)
            if left != right:
                differing = (raw, f"{left!r} != {right!r}")
                break

        if differing:
            print(f"{bug['id']}: REAL DEFECT — differs on random input")
            print(f"    input:  {differing[0]}")
            print(f"    detail: {differing[1]}")
            exit_code = 1
        else:
            print(
                f"{bug['id']}: no observable difference in {N:,} random inputs "
                f"— consistent with an EQUIVALENT mutant"
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
