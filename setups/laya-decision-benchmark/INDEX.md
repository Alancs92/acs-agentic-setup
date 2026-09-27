# setups/laya-decision-benchmark/

A benchmark of the Laya typed-decision model on our own triage and vault decisions, against heuristics
and Claude Haiku, Sonnet and Opus. **Verdict: not adopted.** Raw data and predictions live outside the
repo in `~/.cache/acs-laya-bench/`, because they hold work content.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | The setup: what, why, how to re-run it, privacy rules, known issues. |
| [`results/REPORT.md`](results/REPORT.md) | The committed aggregate report: verdict table, per-task metrics with CIs, cost and latency, calibration, flip rate, caveats. |
| [`results/report.html`](results/report.html) | The same report as a standalone page (open it locally): verdict, headline numbers, precision–coverage charts with hover, and the full tables. Aggregates only, lint-clean against the `usage-dashboard` brand profile. |
| [`results/metrics.json`](results/metrics.json) | The same aggregates, machine-readable. |
| [`results/microbench.json`](results/microbench.json) | Phase 1 smoke test and CPU-vs-MPS micro-benchmark. |
| [`cards/`](cards) | One dataset card per task: source, label provenance, gold/silver counts per split, class balance, paraphrased examples. |
| [`bench/`](bench) | Preflight, dataset builders, systems, runner, metrics (with tests), report and card rendering. |
| [`preflight.json`](preflight.json) | Machine, CLI and data-path snapshot from the run. |

Origin: [`../../research/laya-decision-model-benchmark.md`](../../research/laya-decision-model-benchmark.md).
