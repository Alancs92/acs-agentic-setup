# research/old-coder-benchmark/

The reproducible sandbox behind
[`../old-coder-evidence-first.md`](../old-coder-evidence-first.md). It measures
how many real defects each development-process regime catches, and what each
one costs.

Nothing here is a `sediment` component — `sessionmerge` is a purpose-built
stand-in for that project's riskiest logic (Screenpipe/Meetily overlap dedupe),
sized so a whole bug corpus can be run against it in seconds.

| File | What it is |
|---|---|
| [`spec.md`](spec.md) | The contract. Written first; the implementation, the suites and the bug corpus all derive from it and not from each other. |
| `src/sessionmerge/__init__.py` | The implementation under test — 77 lines of interval merge + coverage-threshold dedupe. |
| `tests/test_suite_a_adhoc.py` | **Suite A** — ad-hoc TDD, no spec artifact. Our current `sediment` standard. |
| `tests/test_suite_b_spec.py` | **Suite B** — one test per spec scenario and Must-NOT. old-coder's SPEC phase alone. |
| `tests/test_suite_c_gauntlet.py` | **Suite C** — B plus a test per surviving mutant, plus hypothesis properties. The full loop. |
| `bugs/corpus.py` | 17 spec-derived defects. 15 confirmed real, 2 confirmed equivalent. |
| `tools/run_bench.py` | Applies each bug, runs every configuration, writes `results.json`. |
| `tools/check_equivalence.py` | Differential-tests a bug against the original over 20k randomized inputs to separate real defects from equivalent mutants. |
| [`results.json`](results.json) | Machine-readable output of the last full run. |

## Reproducing

```sh
cd research/old-coder-benchmark
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -e .

.venv/bin/python tools/run_bench.py          # detection matrix, all configs
.venv/bin/python tools/check_equivalence.py  # real defect vs equivalent mutant
.venv/bin/mutmut run                         # mutation score (needs git)
```

Headline numbers, against the 15 confirmed-real defects:

| Configuration | Caught | Branch coverage | Runtime |
|---|---|---|---|
| Lint (ruff) | 0% | — | 0.01s |
| Types (mypy) | 13% | — | 1.43s |
| A — ad-hoc TDD | 40% | 95% | 0.35s |
| B — spec-driven | 87% | 99% | 0.36s |
| C — + gauntlet | 100% | 99% | 5.89s |

## Two things to know before trusting a rerun

- **`tools/run_bench.py` mutates `src/` in place** and restores it in a
  `finally` block. If it is killed mid-run, `git checkout src/` before
  believing anything.
- **The timezone result only reproduces off UTC.** The `astimezone(None)`
  defect is invisible under `TZ=UTC` (the container and CI default) and fails
  immediately under `TZ=Australia/Sydney`. That divergence is
  [FINDING 3](../old-coder-evidence-first.md#results), not flakiness.
