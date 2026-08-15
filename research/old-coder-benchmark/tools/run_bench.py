"""Benchmark harness.

For every bug in the corpus: apply it, run each gauntlet configuration,
record whether the configuration went red (= bug detected), restore.

A configuration "detects" a bug iff its command exits nonzero.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "sessionmerge" / "__init__.py"
VENV = ROOT / ".venv" / "bin"

sys.path.insert(0, str(ROOT / "bugs"))
from corpus import BUGS  # noqa: E402

CONFIGS = {
    "types (mypy)": [str(VENV / "mypy"), "src", "tests"],
    "lint (ruff)": [str(VENV / "ruff"), "check", "."],
    "A: ad-hoc TDD": [str(VENV / "pytest"), "-q", "-x", "tests/test_suite_a_adhoc.py"],
    "B: spec-driven": [str(VENV / "pytest"), "-q", "-x", "tests/test_suite_b_spec.py"],
    "C: +gauntlet": [
        str(VENV / "pytest"),
        "-q",
        "-x",
        "tests/test_suite_b_spec.py",
        "tests/test_suite_c_gauntlet.py",
    ],
}


def run(cmd: list[str]) -> tuple[bool, float]:
    """Return (went_red, seconds)."""
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode != 0, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", nargs="*", default=None)
    args = parser.parse_args()

    configs = {
        name: cmd
        for name, cmd in CONFIGS.items()
        if args.configs is None or name in args.configs
    }

    original = TARGET.read_text()

    # Baseline: everything must be green on unmutated source, or the
    # measurement is meaningless.
    print("=== baseline (must be all green) ===")
    baseline_cost = {}
    for name, cmd in configs.items():
        red, secs = run(cmd)
        baseline_cost[name] = secs
        status = "RED (INVALID BASELINE)" if red else "green"
        print(f"  {name:<18} {status:<24} {secs:6.2f}s")
        if red:
            print(f"    cmd: {' '.join(cmd)}")
            return 1

    results: dict[str, dict[str, bool]] = {}
    print("\n=== per-bug detection ===")
    try:
        for bug in BUGS:
            if original.count(bug["old"]) != 1:
                raise SystemExit(
                    f"{bug['id']}: anchor not unique "
                    f"({original.count(bug['old'])} matches)"
                )
            TARGET.write_text(original.replace(bug["old"], bug["new"]))
            row = {}
            for name, cmd in configs.items():
                red, _ = run(cmd)
                row[name] = red
            results[bug["id"]] = row
            marks = "".join("X" if row[n] else "." for n in configs)
            print(f"  {bug['id']} [{marks}] {bug['spec_ref']:<6} {bug['desc']}")
    finally:
        TARGET.write_text(original)

    print("\n=== detection rate ===")
    total = len(BUGS)
    summary = {}
    for name in configs:
        caught = sum(1 for row in results.values() if row[name])
        summary[name] = caught
        pct = 100.0 * caught / total
        print(f"  {name:<18} {caught:>2}/{total}  {pct:5.1f}%   "
              f"(baseline runtime {baseline_cost[name]:.2f}s)")

    out = ROOT / "results.json"
    out.write_text(
        json.dumps(
            {
                "configs": list(configs),
                "baseline_seconds": baseline_cost,
                "bugs": {b["id"]: {k: b[k] for k in ("spec_ref", "kind", "desc")}
                         for b in BUGS},
                "detection": results,
                "summary": summary,
                "total_bugs": total,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
