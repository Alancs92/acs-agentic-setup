# Laya decision-model benchmark

- **Created:** 2026-09-27
- **Status:** archived. The benchmark ran and gave a verdict: **Laya not adopted**. The harness is kept so it can be re-run.
- **Agents involved:** Claude Code (executor, plus Haiku/Sonnet/Opus as baselines), Laya 0.3.20 (the system under test)
- **Environment:** local (MacBook, Apple M4 Pro, 48 GB)
- **Topology:** single agent. The Claude baselines are isolated headless calls.

## What this is

A reproducible benchmark of [Laya](https://github.com/NandhaKishorM/laya), an open-weight typed-decision
encoder, on **our own** decisions from the tide-triage and vault tooling. It compares Laya with the
existing heuristics and with Claude Haiku, Sonnet and Opus. It was executed from
[`research/laya-decision-model-benchmark.md`](../../research/laya-decision-model-benchmark.md), which
holds the question, the rules and the stop conditions.

**Result:** see [`results/REPORT.md`](results/REPORT.md), or open [`results/report.html`](results/report.html) for the rendered page with charts. No task reached ADOPT, so nothing is wired
in, not even in shadow mode.

## Why

The question was whether a free, local, ~30 ms classifier could take a useful share of triage decisions
off Claude at a precision we accept. Our gates are strict: 1.00 precision for consuming duplicates, and
0.85–0.95 elsewhere.

## Components

- `bench/preflight.py`: machine, memory, CLI, Ollama, data-repo and Hugging Face checks. Writes `preflight.json`.
- `bench/build_datasets.py`: one builder per task (T1–T6). Reads the vault, its git history and the
  task-manager JSON backups, all read-only. Applies the PHI gate (a port of the vault's pre-commit UID
  check) and a time split. Output goes to `~/.cache/acs-laya-bench/data/`.
- `bench/systems/laya_systems.py`: `laya0`, `laya0-perm`, `layaT` (temperature scaling fitted on cal)
  and `laya-head` (frozen encoder, mean-pooled, plus a logistic-regression head).
- `bench/systems/claude_cli.py`: Claude baselines. One tool-less, settings-less `claude -p` per item,
  run on the logged-in subscription.
- `bench/run.py`: systems × tasks. Resumable; every prediction is cached.
- `bench/metrics.py`: accuracy and macro-F1 with bootstrap CIs, ECE and Brier, coverage at the required
  precision (threshold picked on cal, applied to test, with a Wilson lower bound), flip rate, cost and
  latency. Also holds the Phase 5 verdict rules. `bench/test_metrics.py` tests them.
- `bench/report.py` + `bench/render_html.py`: write `results/REPORT.md`, `results/metrics.json` and
  `results/report.html`, all aggregates only. The HTML page uses the usage-dashboard theme tokens and two
  series hues validated with the dataviz palette checker. It has precision–coverage charts with a hover
  crosshair and passes `impeccable-brand-lint --brand usage-dashboard` with 0 findings. The narrative
  sections live in `bench/report_narrative.json`.
- `bench/cards.py`: writes `cards/<task>.md`, the dataset cards (counts only, paraphrased examples).

## Reproducing it

```bash
cd setups/laya-decision-benchmark
export HF_HOME=~/.cache/acs-laya-bench/hf LAYA_DEVICE=mps
uv venv --python 3.12 ~/.cache/acs-laya-bench/venv
VIRTUAL_ENV=~/.cache/acs-laya-bench/venv uv pip install -r requirements.lock
P=~/.cache/acs-laya-bench/venv/bin/python
$P bench/preflight.py
$P bench/build_datasets.py                      # all tasks; --tasks T1,T3 for a subset
$P bench/run.py --systems majority,heuristic,laya0,laya0-perm,layaT,laya-head
$P bench/run.py --systems haiku                 # then sonnet; opus is capped at 150 test items per task
$P bench/report.py && open results/report.html
$P -m pytest -q bench                           # metric/verdict unit tests
```

Treat a Laya upgrade as a full re-run. The version is pinned to `laya==0.3.20` in `requirements.lock`.

## Privacy

Datasets hold work content: Jira keys, customer and project names, possibly PHI-adjacent text. Raw
data, predictions, logs and the HTML report stay in `~/.cache/acs-laya-bench/` and are **never
committed**. The repo holds only code, cards (counts and paraphrases) and aggregate metrics. Every
record passes the PHI gate before it is written. Records go to Claude through `claude -p`, as the
triage agent already does, and to no other third party.

## Notes / known issues

- **The live board was unreachable.** The Cloudflare account on this machine got 7403 on the D1
  database. All board data comes from the 2026-08-26 pre-purge backups. As a result, T2 (triage
  action) and T4 (domain) have too few human-labelled items and are exploratory only.
- **The T1 negatives had to be cleaned.** Some "hard negatives" were tasks created *from* the signal
  during the same runs, others were board near-copies of the true target, and "no_task" signals had been
  dismissed as done rather than as different work. See `cards/T1.md`.
- **T3 labels leak through template headings.** "Context / Symptom" marks a gotcha, for example, so
  sub-headings are stripped from the state.
- **T6 can't be labelled from git history.** No broken link was ever fixed by creating its target, so
  there are no positives.
- `laya-evals` isn't in the 0.3.20 wheel, so `common.validate_record` stands in for `laya-evals validate`.
- `--max-turns 2` (not 1): structured output uses one extra internal turn.
