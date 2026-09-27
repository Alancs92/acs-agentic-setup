# Laya decision-model benchmark — results

**Laya is not worth adopting: no task reached ADOPT on our own gold decisions.** Phase 6 skipped.
Zero-shot Laya is far below every useful bar on our data (T1 duplicate coverage 7%, T3 accuracy 0.41 vs 0.66 majority, T5 at chance). A trained head on its frozen encoder gets T1 to 57% at 1.00 precision, which is still *below* the free Jaccard matcher already in production (68%).
Useful regardless: on T1, Opus is the only Claude tier that held 1.00 precision (79% coverage); Haiku and Sonnet each made a false consume at their cal-chosen threshold. No tier clears T3 or T5 at the required precision.

## Verdict table

Coverage = share of the work auto-actioned at the task's required precision, threshold chosen on `cal` and applied to gold `test` (LB = Wilson 95% lower bound on precision at that point). For yes/no tasks coverage is the share of true positives auto-actioned.

| Task | Req. prec. | Verdict | Best Laya | Laya coverage | Heuristic | Haiku | Sonnet | Opus |
|---|---|---|---|---|---|---|---|---|
| T1 Duplicate of task X | 1.00 | **DROP** | laya-head | 57% (LB 0.81) | 68% (LB 0.83) | 79% (LB 0.79) | 82% (LB 0.71) | 79% (LB 0.85) |
| T3 Handbook note type | 0.90 | **DROP** | layaT | 3% (LB 0.15) | n/a | 80% (LB 0.73) | 90% (LB 0.78) | 99% (LB 0.80) |
| T5 Related-note relevance | 0.85 | **DROP** | laya-head | 0% (LB –) | 0% (LB –) | 22% (LB 0.61) | 17% (LB 0.57) | 27% (LB 0.71) |
| T2 Triage action | 0.95 | **EXPLORATORY** | laya-head | 14% (LB 0.00) | n/a | 0% (LB –) | 0% (LB –) | not run |
| T4 Task domain | 0.90 | **EXPLORATORY** | laya-head | 92% (LB 0.73) | n/a | 33% (LB 0.76) | 67% (LB 0.80) | not run |
| T6 Broken link | 0.95 | EXPLORATORY (not run) | – | – | – | – | – | – |

**Why, per task:**

- **T1** — fails rule 1: >=30% coverage with LB >= req-0.05, rule 2: +10 pts over the heuristic: laya-head covers 57% at precision 1.00 (LB 0.81) vs required 1.00; heuristic 68%. No Claude tier passes either.
- **T3** — fails rule 1: >=30% coverage with LB >= req-0.05: layaT covers 3% at precision 0.50 (LB 0.15) vs required 0.90. No Claude tier passes either.
- **T5** — fails rule 1: >=30% coverage with LB >= req-0.05, rule 2: +10 pts over the heuristic: laya-head covers 0% at precision n/a (LB n/a) vs required 0.85; heuristic 0%. No Claude tier passes either.
- **T2** — 7 gold test items (< 40, S3).
- **T4** — 36 gold test items (< 40, S3).
- **T6** — not run: history-derived labels are single-class (no broken link was ever fixed by creating its target in the snapshot window). See `cards/T6.md`.

## Per-task metrics (gold test)

### T1 — Duplicate of task X (n gold test = 121)

| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | Precision (LB) | Abstain |
|---|---|---|---|---|---|---|---|---|---|
| majority | 121 | 0.769 [0.69, 0.84] | 0.435 [0.41, 0.46] | 0.231 | 0.231 | – | 0% | – (–) | 0 |
| heuristic | 121 | 0.926 [0.88, 0.97] | 0.881 [0.80, 0.95] | 0.097 | 0.084 | 0.421 | 68% | 1.00 (0.83) | 0 |
| laya0 | 121 | 0.711 [0.63, 0.79] | 0.686 [0.60, 0.77] | 0.258 | 0.203 | 0.951 | 7% | 1.00 (0.34) | 0 |
| layaT | 121 | 0.711 [0.63, 0.79] | 0.686 [0.60, 0.77] | 0.274 | 0.195 | 0.794 | 7% | 1.00 (0.34) | 0 |
| laya-head | 121 | 0.934 [0.89, 0.97] | 0.902 [0.83, 0.96] | 0.048 | 0.057 | 0.963 | 57% | 1.00 (0.81) | 0 |
| haiku | 121 | 0.926 [0.88, 0.97] | 0.891 [0.82, 0.95] | 0.073 | 0.056 | 0.800 | 79% | 0.96 (0.79) | 0 |
| sonnet | 121 | 0.901 [0.85, 0.95] | 0.875 [0.81, 0.94] | 0.100 | 0.067 | 0.900 | 82% | 0.88 (0.71) | 0 |
| opus | 121 | 0.917 [0.87, 0.96] | 0.892 [0.83, 0.95] | 0.101 | 0.053 | 0.880 | 79% | 1.00 (0.85) | 0 |

### T3 — Handbook note type (n gold test = 142)

| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | Precision (LB) | Abstain |
|---|---|---|---|---|---|---|---|---|---|
| majority | 142 | 0.655 [0.58, 0.72] | 0.198 [0.18, 0.21] | 0.345 | 0.690 | – | 0% | – (–) | 0 |
| laya0 | 142 | 0.408 [0.33, 0.49] | 0.353 [0.27, 0.44] | 0.086 | 0.693 | 0.527 | 18% | 0.40 (0.23) | 0 |
| layaT | 142 | 0.415 [0.34, 0.50] | 0.356 [0.27, 0.44] | 0.137 | 0.725 | 0.898 | 3% | 0.50 (0.15) | 0 |
| laya-head | 142 | 0.725 [0.66, 0.80] | 0.370 [0.29, 0.45] | 0.132 | 0.433 | 0.893 | 11% | 1.00 (0.81) | 0 |
| haiku | 142 | 0.761 [0.69, 0.82] | 0.528 [0.42, 0.64] | 0.113 | 0.395 | 0.820 | 80% | 0.82 (0.73) | 0 |
| sonnet | 142 | 0.824 [0.76, 0.89] | 0.658 [0.53, 0.77] | 0.086 | 0.284 | 0.720 | 90% | 0.85 (0.78) | 0 |
| opus | 142 | 0.866 [0.81, 0.92] | 0.668 [0.55, 0.78] | 0.064 | 0.219 | 0.500 | 99% | 0.87 (0.80) | 0 |

Option-order flip rate (laya0, 3 shuffles): **31%**.

### T5 — Related-note relevance (n gold test = 244)

| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | Precision (LB) | Abstain |
|---|---|---|---|---|---|---|---|---|---|
| majority | 244 | 0.529 [0.47, 0.59] | 0.346 [0.32, 0.37] | 0.471 | 0.471 | – | 0% | – (–) | 0 |
| heuristic | 244 | 0.590 [0.53, 0.65] | 0.499 [0.44, 0.56] | 0.265 | 0.296 | 1.000 | 0% | – (–) | 0 |
| laya0 | 244 | 0.492 [0.43, 0.55] | 0.431 [0.38, 0.49] | 0.313 | 0.345 | – | 0% | – (–) | 0 |
| layaT | 244 | 0.492 [0.43, 0.55] | 0.431 [0.38, 0.49] | 0.044 | 0.251 | – | 0% | – (–) | 0 |
| laya-head | 244 | 0.541 [0.48, 0.61] | 0.388 [0.34, 0.44] | 0.051 | 0.243 | 0.520 | 0% | – (–) | 0 |
| haiku | 244 | 0.652 [0.59, 0.71] | 0.646 [0.59, 0.71] | 0.114 | 0.220 | 0.800 | 22% | 0.78 (0.61) | 0 |
| sonnet | 244 | 0.660 [0.60, 0.72] | 0.649 [0.58, 0.71] | 0.197 | 0.246 | 0.820 | 17% | 0.76 (0.57) | 0 |
| opus | 150 | 0.727 [0.65, 0.79] | 0.727 [0.65, 0.79] | 0.117 | 0.181 | 0.850 | 27% | 0.90 (0.71) | 0 |

### T2 — Triage action (n gold test = 7, silver = 27)

| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | Precision (LB) | Abstain |
|---|---|---|---|---|---|---|---|---|---|
| majority | 7 | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 1.000 | 2.000 | – | 0% | – (–) | 0 |
| laya0 | 7 | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.353 | 0.962 | – | 0% | – (–) | 0 |
| layaT | 7 | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.223 | 0.822 | – | 0% | – (–) | 0 |
| laya-head | 7 | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.912 | 1.666 | 0.936 | 14% | 0.00 (0.00) | 0 |
| haiku | 7 | 0.429 [0.14, 0.71] | 0.120 [0.05, 0.17] | 0.496 | 0.793 | – | 0% | – (–) | 0 |
| sonnet | 7 | 1.000 [1.00, 1.00] | 0.200 [0.20, 0.20] | 0.283 | 0.116 | – | 0% | – (–) | 0 |

Option-order flip rate (laya0, 3 shuffles): **30%**.

Silver-only accuracy (Claude-labelled, circular for Claude baselines): majority 0.70, laya0 0.26, layaT 0.26, laya-head 0.70, haiku 0.15, sonnet 0.15.

### T4 — Task domain (n gold test = 36, silver = 30)

| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | Precision (LB) | Abstain |
|---|---|---|---|---|---|---|---|---|---|
| majority | 36 | 0.833 [0.72, 0.94] | 0.182 [0.17, 0.19] | 0.167 | 0.333 | – | 0% | – (–) | 0 |
| laya0 | 36 | 0.500 [0.33, 0.67] | 0.311 [0.12, 0.47] | 0.189 | 0.624 | – | 0% | – (–) | 0 |
| layaT | 36 | 0.500 [0.33, 0.67] | 0.311 [0.12, 0.47] | 0.175 | 0.584 | – | 0% | – (–) | 0 |
| laya-head | 36 | 0.833 [0.72, 0.94] | 0.390 [0.18, 0.40] | 0.107 | 0.280 | 0.564 | 92% | 0.88 (0.73) | 0 |
| haiku | 36 | 0.944 [0.86, 1.00] | 0.593 [0.39, 0.60] | 0.079 | 0.115 | 0.980 | 33% | 1.00 (0.76) | 0 |
| sonnet | 36 | 0.778 [0.64, 0.92] | 0.589 [0.30, 0.68] | 0.136 | 0.319 | 0.750 | 67% | 0.96 (0.80) | 0 |

Option-order flip rate (laya0, 3 shuffles): **21%**.

Silver-only accuracy (Claude-labelled, circular for Claude baselines): majority 0.93, laya0 0.47, layaT 0.47, laya-head 0.87, haiku 0.93, sonnet 0.90.

## Cost and latency per 1,000 decisions

| System | p50 latency / decision | p95 | API-equivalent $ / 1k | Where it runs |
|---|---|---|---|---|
| laya0 | 60 ms | 79 ms | $0.00 | MacBook (MPS), $0 marginal |
| laya-head | 46 ms | 54 ms | $0.00 | MacBook (MPS), $0 marginal |
| haiku | 7329 ms | 14043 ms | $6.80 | Claude subscription (notional $) |
| sonnet | 2344 ms | 4636 ms | $6.85 | Claude subscription (notional $) |
| opus | 3290 ms | 6146 ms | $9.47 | Claude subscription (notional $) |

Claude latency is the envelope `duration_ms`; CLI start-up overhead adds ~3353 ms wall per call. Per-call input overhead (system prompt, cached) ≈ 7512 tokens. Laya peak RSS: 1.38 GB on MPS, 2.39 GB on CPU (`results/microbench.json`).

## Calibration

| Task | laya0 ECE | layaT ECE | fitted T | laya-head ECE |
|---|---|---|---|---|
| T1 | 0.258 | 0.274 | 2.20 | 0.048 |
| T3 | 0.086 | 0.137 | 0.60 | 0.132 |
| T5 | 0.313 | 0.044 | 19.70 | 0.051 |
| T2 | 0.353 | 0.223 | 5.67 | 0.912 |
| T4 | 0.189 | 0.175 | 0.68 | 0.107 |

## Option-order flip rate

Share of (item, shuffle) pairs where zero-shot Laya changed its answer when only the option order changed: T3 31%, T2 30%, T4 21%.

## Surprises and caveats

- **T1's gate cannot be proven on this data by anyone.** With 28 true duplicates in gold test, a perfect run's Wilson lower bound is 0.88 < 0.95 (rule 1). Verdicts on T1 are therefore comparative: Laya-head (57%) loses to the existing heuristic (68%) at the same observed precision, which fails rule 2 independently.
- **The T1 fixture needed cleaning before it could be used as pairs.** Hard negatives included tasks created *from* the signal during runs 4–5 (leakage), board near-copies of the true target (ambiguous), and `no_task` signals dismissed because the work was *done* (not different work). All three were removed; see `cards/T1.md`. The matcher spec already noted the same fixture had been wrong once before.
- **T3 label leakage through template headings** (`Context / Symptom` ⇒ gotcha). Sub-headings are stripped. With them left in, every system would look better than it is at write-time.
- **T5's negatives are "suggested but not kept", which is not the same as irrelevant.** Many plausible links were simply not chosen, so the task has a noisy ceiling: even Opus reaches only 0.73 accuracy. The heuristic score is anti-informative by construction (negatives are its own top picks).
- **Flip rate is high:** 21–31% of zero-shot Laya answers change when only the option order changes, on 4–5 options — worse than the 15–23% the upstream README reports at 20 options.
- **Calibration is not the problem.** Temperature scaling barely moves coverage; Laya's zero-shot ranking itself does not separate right from wrong answers on our decisions.
- **Silver labels:** used only in T2 (auto-applied outcomes from runs 4–5) and in T4 (tasks never revisited). Both tasks are exploratory and excluded from verdicts. T4's `laya-head` 92% coverage at 0.88 precision (36 gold items, heavy `harrison` majority) is the only faint positive and is not statistically meaningful.
- **Claude confidence is self-reported** and coarse (clusters at 0.8–0.95), so its ECE is reported as such.
- **Latency:** Laya is ~100× faster (24–70 ms vs 2.3–8.5 s) and free, but speed does not matter when it cannot clear the precision gates.

## What was not run, and why

- **T6 (broken link real/placeholder):** git history yields no positive labels (no broken link was fixed by creating its target in the four snapshots), so precision/coverage are undefined. Needs a human-labelled set to be meaningful.
- **T2 and T4 verdicts:** exploratory under S3 (7 and 36 gold test items). The live D1 board, which would have given more recent labelled signals and tasks, returned Cloudflare 7403 for the account on this machine; the 2026-08-26 backups were used instead.
- **Opus on T2/T4:** skipped (exploratory tasks). Opus on T5 used a stratified 150-item test sample (and 60 cal), per the runbook cap; T1 and T3 were small enough to run in full.
- **Ollama baseline:** optional; skipped — the smallest pulled instruct model is 27B (17 GB).
- **laya-apple / MLX:** not tried — PyTorch-MPS p50 was already 29 ms, far below the 400 ms trigger.
- **Full Laya fine-tuning (RLCD notebook):** out of scope. Not listed as a follow-up: laya-head did not win any task narrowly, and on T1 even a win would only replace a free deterministic matcher.
- **`task category` (T4):** skipped — it is a kanban column (a state over time), not a property of the task text.

## Exact commands, versions, machine

- Machine: Apple M4 Pro, 48 GB, 10 performance cores, macOS (Darwin 25.6). Memory free at start 83%; Docker/Ollama left running.
- Laya 0.3.20, checkpoint `convaiinnovations/laya` (english) snapshot `55cf4c4`; torch 2.14.0; Python 3.12.12 (uv); device MPS, `torch.set_num_threads(10)`, `set_num_interop_threads(1)`.
- Claude Code 2.1.283; models resolved to `claude-haiku-4-5-20251001`, `claude-sonnet-5`, `claude-opus-5-5`. Max 4 concurrent calls, empty temp cwd.
- Claude command line (per item): `claude -p <PROMPT> --model <MODEL> --output-format json --json-schema <SCHEMA> --max-turns 2 --tools '' --setting-sources '' --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence --append-system-prompt 'You are a classifier. Answer only with the requested JSON.'`
- Pipeline: `bench/preflight.py` → `bench/build_datasets.py` → `bench/run.py` (local systems, then `--systems haiku|sonnet|opus`) → `bench/report.py`. Full lock in `requirements.lock`.

## Shadow-mode integration

**None.** No task reached ADOPT, so Phase 6 was skipped: no `claude-acs laya` subcommand, no `laya-serve`, no `ACS_LAYA` hooks in the matcher, triage pre-pass, `related_suggest.py` or `vault_audit.py`. There is no flag to flip.

**Re-open criteria:** re-run this harness if (a) a Laya release ships a materially better English checkpoint (treat as a full re-run), (b) a fine-tuned checkpoint on our own decisions becomes available, or (c) the live board becomes reachable and T2 gains ≥ 40 human-answered test items.
