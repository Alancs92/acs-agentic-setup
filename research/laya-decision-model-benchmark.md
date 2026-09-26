<!--
Runbook-style research note. It is written to be EXECUTED by Claude Code on
Alan's MacBook, end to end, with no further input. Kick-off prompt is in
"How to run this". Results get appended to the "Results" section at the end.
-->

# Laya decision model — own benchmark, and adopt-or-drop per task

- **Date:** 2026-09-26
- **Status:** open — runbook ready, awaiting execution on the MacBook
- **Agents involved:** Claude Code (executor + Haiku/Sonnet/Opus baselines), Laya (system under test), Ollama (optional baseline)

## How to run this

Open this repo in Claude Code on the MacBook and send:

> Read `research/laya-decision-model-benchmark.md` and execute the runbook end to end,
> autonomously. Only stop for the stop conditions it lists. Finish with the report.

What comes back:

- A verdict table saying, for each task, **adopt / adopt with a trained head / drop**, and why.
- For every adopted task, the integration merged in **shadow mode**. Shadow mode logs what Laya would have decided and changes nothing. The report ends with the one flag to flip to make it act.

Everything below is addressed to the executor.

---

## Question

[Laya](https://github.com/NandhaKishorM/laya) is an open-weight (Apache 2.0) *decision* model. It answers typed questions (`choice`, `score`, `noul` = yes/no) about a piece of text in one encoder forward pass, and it never generates text. It came out on 2026-09-18 as an open alternative to TypeSafe AI's hosted Jev.

The questions are:

1. Can it run locally on the MacBook without getting in the way of Docker and Ollama?
2. On **our own** decisions, not public leaderboards, does it take a useful share of the work off Claude at a precision we accept?
3. If it does, for which decisions, and wired in where?

## Findings so far (desk research, 2026-09-26, Laya v0.3.20 @ `4066d5d`)

**What it is**

- **Checkpoints.** Three, all in HF repo `convaiinnovations/laya` as subfolders:
  - `english`: ModernBERT-large, 421M params, 512-token context, of which 192 tokens are the option/question "head budget".
  - `multilingual`: mmBERT-base, 322M, 1,024 tokens.
  - `typed-decisions`: 421M, fine-tuned on the train split of the typed-decisions benchmark.
- **Router.** It picks the checkpoint by script and language. Our data is English, so pin `model="english"` and load only that one.
- **API.** `from laya import Router; Router(max_loaded=1, device=...).predict(state, questions, model="english")`.
  - `choice` answers carry `choice`, `probabilities`, `confidence` and `answer_confidence`.
  - `noul` answers carry `noul`, which is P(yes).
  - **Gate on `answer_confidence`**, not `confidence`. `confidence` is 1 minus normalised entropy and is not comparable across option counts.
- **Tooling.** `laya-evals` (JSONL eval runner), `laya-serve` (Jev-compatible `POST /v1/systemone` on :8000, `LAYA_THREADS`), `laya-mcp-server`.
- **Apple port.** The community project [laya-apple](https://github.com/tc3oliver/laya-apple) runs it on MLX/ANE. It is optional and has not been verified here.

**How good it is**

- **Zero-shot is weak on typed decisions.** On the typed-decisions benchmark it scores 0.362, below the majority class (0.461). The widely quoted "beats Jev" 0.766 is the checkpoint *fine-tuned on that benchmark's own train split*. The repo's own verdict: "a fast base to specialise, not a zero-shot engine."
- **Strong:** simple topic classification (AG News 0.95).
- **Weak:**
  - Many options. Banking77 scores 0.43, because once there are more than about 20 options each label gets 3–4 tokens.
  - Held-out moderation: about chance.
  - Support-ticket triage: about 0.5.
  - Ordinal `score`: 0.37.
  - Option-order flips of 15–23% at 20 options.
- **Over-confident as shipped.** Temperature fitting per (question type, option count) takes ECE from 0.466 to 0.081. You must calibrate on our data.

**Performance**

- **Latency.**
  - T4 GPU: about 33–40 ms per question.
  - 4-core server CPU: about 580 ms.
  - Unpinned threads: a 3-question call took **9.4 s**. `torch.set_num_threads(<physical cores>)` plus `torch.set_num_interop_threads(1)` brought it to 0.78 s.
  - Apple GPU: about 1.7 s for a 4k-token input.
- **Memory.** About 2 GB for one checkpoint. The "under 1 GB RAM" press claim is unverified.

**Where it could help in our stack.** The integration points found are listed as the tasks in Phase 2. Our strongest assets are that we already run **two-tier confidence gating**, keep **human-labelled decision logs**, and have a **100%-precision gate** on duplicate consumption. Those are exactly what a calibrate-then-threshold System-1 model needs.

---

## Executor contract (read before doing anything)

### Autonomy and stop conditions

Run every phase without asking. Stop and report only in these cases:

- **S1:** fewer than **6 GB** of memory is free after Phase 0's check, and it stays that way after waiting 10 minutes. Never stop Docker or Ollama to make room.
- **S2:** a `claude -p` smoke call fails on auth, or usage limits block the Claude baselines for more than 1 hour. In that case finish everything else and mark those cells `not run`.
- **S3:** a task's gold test split has fewer than **40 items**. Don't stop the whole run for this: run the task as `exploratory`, exclude it from the verdict, and continue.
- **S4:** anything would write to the **live task board** (D1 via the tracker CLI), or would consume, dismiss or move a signal. Benchmarks are strictly read-only against the board.

### Privacy rules

This is the same rule as `setups/claude-code-usage-stats`.

- Datasets are built from the vault and the task board. They contain work content: Jira keys, customer and project names, possibly PHI-adjacent text from medical-imaging work.
- Raw data, predictions and per-item outputs live in `~/.cache/acs-laya-bench/` and are **never committed**.
- The repo gets code, dataset *cards* (counts, label distributions, sources), and aggregate metrics only.
- Before a record is written to any dataset, run it through the vault's PHI regex gate: `acs-anamnesis/scripts/hooks/pre-commit`, reusing its patterns. Drop any record that trips it.
- Sending records to Claude through `claude -p` is acceptable. This is already how the triage agent sees them. Don't send them to any other third-party service.

### Memory and Mac hygiene

- **Don't assume 48 GB is free.** Docker and Ollama are often running.
- Record `memory_pressure` and `vm_stat` at the start of each phase.
- Load **one** Laya checkpoint (`Router(max_loaded=1)`, `model="english"`).
- Never run Laya and an Ollama model inference at the same time.
- Run Laya CPU threads at physical performance cores; read `sysctl hw.perflevel0.physicalcpu`.

### Where things go

```
setups/laya-decision-benchmark/          ← committed (code, cards, aggregates)
  README.md                              ← setup-template shape; how to re-run
  INDEX.md
  pyproject.toml / requirements.lock     ← pinned: laya==0.3.20, torch, scikit-learn, numpy
  bench/
    preflight.py        env + memory + CLI checks → preflight.json
    build_datasets.py   one builder per task → ~/.cache/acs-laya-bench/data/<task>/{train,cal,test}.jsonl
    systems/            majority.py, heuristic_*.py, laya_zero.py, laya_calibrated.py,
                        laya_head.py, claude_cli.py, ollama.py (optional)
    run.py              runs systems × tasks, resumable, caches every prediction
    metrics.py          accuracy, macro-F1, ECE, precision/coverage curves, bootstrap CIs
    report.py           → results/REPORT.md (aggregates only) + ~/.cache/…/report.html
  cards/<task>.md       dataset card: source, label provenance (gold/silver), n per split, class balance
  results/REPORT.md     committed aggregate report
~/.cache/acs-laya-bench/                 ← NEVER committed: venv, HF cache, data, predictions, logs
  RUNLOG.md             append-only log of every phase, decision and deviation
```

- Add the setup to `setups/INDEX.md` in the same commit, following the repo's navigation contract.
- If `bench/` grows beyond this, that's fine. Keep it self-contained in the setup.

### Git

- Work on a branch in `acs-agentic-setup`.
- Commit per phase with conventional messages, open a PR, and merge it when Phase 7 is done. This mirrors how this runbook itself landed.
- Integration changes in other repos (Phase 6) follow each repo's own conventions, with a branch and PR per repo. Because shadow mode changes no behaviour, merging is allowed. Say in each PR body that it is shadow-only.

---

## Phase 0 — Preflight (≈5 min)

Write `preflight.json` and append a summary to RUNLOG. Check the following.

**1. The machine.**

- `uname -m` is `arm64`.
- Record the chip model, total RAM, performance-core count, free memory and memory pressure.
- Free disk must be at least **8 GB** (venv plus torch plus about 1.7 GB of weights plus cache).

**2. Python.** Use `uv` if present, else `python3 -m venv`, with Python **3.11 or 3.12**. Avoid 3.14 wheels on macOS unless torch has them.

**3. Claude CLI.**

- `claude --version`.
- Run one smoke call per model alias (Phase 3 has the exact invocation), and record `total_cost_usd` and latency.
- A failed Opus call is not fatal: mark it and continue (S2).

**4. Ollama (optional).**

- Check whether `ollama list` works.
- Pick the **smallest instruct model already pulled** (for example a qwen3 or llama 7–8B). Never pull a new model over 10 GB.
- If none is pulled, skip Ollama.

**5. Locate the data repos.** Search in order: `$ACS_VAULT`, then the usual clone roots (`~/Developer`, `~/code`, `~/src`, `~/Documents`, `~/Obsidian`, `~`). Match by these markers:

- **Vault:** the directory containing `_MOC.md` and `Projects/acs-task-manager/`.
- **Task-manager code:** the repo that provides `cli/tracker`. `Projects/acs-task-manager/INDEX.md` and `architecture/` say where it lives.
- **Skills source:** `Projects/claude-skills/` in the vault, and `~/.claude/skills`.

Record the absolute paths in `preflight.json`. If the vault cannot be found, that is a hard stop: report what you searched.

**6. Hugging Face.** Confirm `huggingface.co` is reachable. The first Laya load downloads the weights.

## Phase 1 — Install and smoke test Laya (≈15 min)

**1. Install.**

- Create the venv in `~/.cache/acs-laya-bench/venv`.
- `pip install "laya==0.3.20" scikit-learn numpy`.
- Freeze to `requirements.lock`.
- Set `HF_HOME=~/.cache/acs-laya-bench/hf`.

**2. Load.** Call `Router(max_loaded=1, device=D)` with `D ∈ {"mps", "cpu"}`. For CPU, first call:

```python
import torch
torch.set_num_threads(<perf cores>)
torch.set_num_interop_threads(1)
```

**3. Smoke-test** with the README example: the billing-department `choice`, the urgency `score` and the churn `noul`. Assert the answer is `billing`.

**4. Micro-benchmark on each device.**

- 2 warm-up calls, then 30 timed calls: 15 with a 3-option `choice` plus a `noul` question, and 15 with an 8-option `choice`.
- Also run `predict_batch` with batch size 16.
- Record p50/p95 latency and **peak RSS** (`/usr/bin/time -l` or `psutil`).
- Pick the faster device as `LAYA_DEVICE` for everything that follows, and record why.

**5. Decide whether to try laya-apple.** Only if the best p50 is above 400 ms per question and a quick trial installs cleanly: try laya-apple as a third runtime. Keep it only if its answers match the PyTorch path on the smoke set within 0.02 probability. Otherwise note "not tried" or "rejected" and move on.

**Gate:** if the smoke test fails on both devices, stop and report the traceback. Nothing downstream is meaningful without it.

## Phase 2 — Build the datasets (the benchmark itself)

### Label quality: gold and silver

Every record carries `label_source: gold | silver`.

- **gold** means a human decided it. Examples: a review-side answer or an override in `decisions-log.md`, the human-reviewed backtest mappings, or a note's frontmatter `type` written or kept by Alan.
- **silver** means the triage agent (Claude) decided it and no human reviewed it.

**Verdicts are computed on gold test items only.** Silver items can be used for training heads and fitting calibration, but never for the headline numbers. The reason: the Claude baselines would be graded against Claude's own earlier answers, a circular advantage. Report silver-only numbers separately, clearly labelled.

### Splits

Split **by time**, never at random.

- Train and calibration come from the older 70%.
- **test is the newest 30%.**
- Calibration is the last 20% of the train window.

Record the cut dates in the card.

### Record schema

The schema is compatible with `laya-evals`. `laya-evals validate` must pass on every file. If it rejects the extra `id` and `ts` keys, move them into `tags` (`id:…`, `ts:…`) or a sidecar file:

```json
{"id": "...", "state": {"text": "...", "context": "..."}, "questions": {"q": {...}},
 "expected": {"q": "<label | number | true/false>"}, "tags": ["label:gold", "src:decisions-log"],
 "ts": "YYYY-MM-DD"}
```

**Keep `state` under about 300 tokens**, because the English checkpoint has 512 tokens in total and 192 of them are the head budget.

- Truncate with a head-plus-tail strategy.
- Record the share of records that were truncated.
- If more than 25% of a task's records need truncation, also run that task once with `model="multilingual", max_len=1024` and report both.

### The tasks

Build them in this order. Each gets a builder in `build_datasets.py` and a card in `cards/`.

**T1 — Signal is a duplicate of task X (`noul`).** Highest value.

- **Source:**
  - The ~90 human-reviewed signal→task mappings from runs 4 and 5 (signals 1127–1246), described in `Projects/acs-task-manager/specs/2026-08-26-bulk-duplicate-matcher-design.md` (§Validation).
  - Plus review-side answers in `triage-agent/decisions-log.md`.
  - Pull the signal and task texts through the tracker CLI (read-only list/get commands) or from the JSON dumps in `Projects/acs-task-manager/backups/`.
- **Negatives:** for each signal, the top 3 non-matching board tasks by the matcher's own Jaccard score. These are hard negatives.
- **State:** `{"signal": <suggestion + evidence>, "task": <task title + description>}`.
- **Question:** "Is the signal asking for the same work as this existing task?"
- **Heuristic baseline:** the matcher's Jaccard score itself.
- **Required precision:** **1.00**. This is the spec's gate: a single false consume fails.

**T2 — Triage action (`choice`, 5 options).**

- **Options:** `dismiss` / `duplicate` (consume) / `move` / `add` / `idea`.
- **Source:** per-signal outcomes recorded in `decisions-log.md` entries, and `pending-review.jsonl` items that have a human answer. The tracker may also expose consumed signals with a resolution; inspect the tracker CLI's `--help`.
  - **gold:** review-side, answered by Alan, including overrides.
  - **silver:** auto-applied.
- **State:** the signal text. Include the top-3 candidate board tasks only if they fit the token budget.
- **Heuristic baseline:** majority class, and "always review".
- **Required precision:**
  - **0.95** on `add`, `move`, `duplicate` and `idea`.
  - **`dismiss` is excluded from auto-action entirely.** Per the triage spec, *ambiguity never auto-dismisses* and dismiss is the one irreversible action. Laya may *recommend* dismiss, but it is never counted as automatable.

**T3 — Handbook note type (`choice`, 4 options).**

- **Options:** `gotcha` / `decision` / `risk` / `invariant`.
- **Source:** every vault note whose frontmatter `type:` is one of these (≈470; the counts were 272/97/33/70 at time of writing).
  - **gold:** notes in `handbook/` folders.
  - Split by the note's `created`/`date` frontmatter, else by the git first-commit date.
- **State:** the note title plus its first ~250 tokens of body, **with the frontmatter and the folder path stripped** so the label doesn't leak.
- **Required precision:** **0.90**. This is a low-stakes suggestion task.

**T4 — Task domain / category (`choice`).**

- **Source:** board tasks with their assigned domain and category (tracker read-only), and the canonical category list in `Projects/acs-agentic-setup/research/acs-task-manager-triage-heuristics.md`.
- **gold** means the value survived a human edit or review. Otherwise it's silver.
- **More than 15 categories:** also run a two-stage variant. Laya's `predict_shortlist`, or domain first and then category within it. This is its known weak spot.
- **Required precision:** **0.90**.

**T5 — Related-note relevance (`noul`).**

- **Source:**
  - **Positives:** pairs (note, linked note) that already exist in curated `## Related` sections.
  - **Negatives:** candidates that `Projects/claude-skills/vault-related/scripts/related_suggest.py` proposes for the same note but that were *not* kept. Run the suggester read-only.
  - All of these are gold only if the Related section was curated by a human or the skill. Label that provenance as found.
- **Heuristic baseline:** the suggester's additive score.
- **Required precision:** **0.85**.

**T6 — Broken wikilink: real or placeholder (`noul`).**

- **Source:** `vault_audit.py`'s broken-link list. It is labelled by later git history: the link was fixed or created (real) versus deleted or left as a template (placeholder). Use the existing `is_placeholder` regex (`vault_audit.py`) as the baseline.
- **Required precision:** **0.95**.
- **If fewer than 40 gold items can be derived, mark T6 exploratory (S3).**

For each task, write the card before running any system:

- The source, how the labels were derived, and gold/silver counts per split.
- The class balance, and the truncation rate.
- **Five example records**, paraphrased so that no raw work content lands in the repo.

## Phase 3 — Systems under test

Every system implements `predict(records) -> [{"id", "label", "p": {label: prob} | p_yes, "latency_ms", "cost_usd"}]`. Predictions are cached to `~/.cache/acs-laya-bench/preds/<task>/<system>.jsonl`, so a re-run resumes.

| ID | System | Notes |
|---|---|---|
| `majority` | Most frequent train label | Floor |
| `heuristic` | The existing rule for that task | Jaccard (T1), `related_suggest` score (T5), `is_placeholder` (T6); `n/a` elsewhere |
| `laya0` | Laya zero-shot, shipped temperatures | `model="english"`, questions exactly as in Phase 2 |
| `laya0-perm` | Same, option order shuffled (3 seeds) | Measures option-order flip rate; `choice` tasks only |
| `layaT` | `laya0` + temperature scaling fitted on `cal` | Fit one T per task on log-probs: minimise NLL; apply `p ∝ p_raw^(1/T)`. Model-agnostic — don't depend on Laya internals. |
| `laya-head` | Frozen Laya encoder + logistic-regression head trained on `train` | Try in order: (a) [stuntd](https://github.com/bladedevoff/stuntd) if it installs and exposes embeddings; (b) pooled last hidden state from the `Agent`'s underlying HF encoder (inspect the object returned by `Router.load("english")`); (c) if neither works in 30 min, record `not run` + why. Calibrate on `cal`. |
| `haiku` / `sonnet` / `opus` | Claude via Claude Code headless | See the invocation below. |
| `ollama` | Smallest local instruct model already pulled | Optional; same prompt as Claude, JSON-only output, `temperature 0`. |

**Full Laya fine-tuning** (the repo's RLCD notebook on Kaggle's 2×T4) is **out of scope** for this run. If `laya-head` wins a task narrowly, list full fine-tuning as a follow-up in the report.

### Claude baseline invocation

Each item is one isolated, tool-less, context-free call. That makes the Claude numbers a fair classifier baseline: not inflated by the user's skills and CLAUDE.md, and cheap.

```bash
cd "$(mktemp -d)"            # empty cwd: no project CLAUDE.md, no repo context
claude -p "$PROMPT" \
  --model "$MODEL" \
  --output-format json \
  --json-schema "$SCHEMA" \
  --max-turns 1 \
  --disallowedTools "Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Agent,Skill" \
  --setting-sources "" \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --append-system-prompt "You are a classifier. Answer only with the requested JSON."
```

- **`$MODEL`** is `haiku`, `sonnet` or `opus` (aliases). Record the resolved model id from the envelope.
- **`$SCHEMA`** is a per-task JSON Schema, for example `{"label": enum[...], "confidence": number 0..1}` or `{"p_yes": number 0..1}`.
- **Don't use `--bare`.** It skips CLAUDE.md, skills and plugins, but it also **does not read the OAuth login**, so it would need an API key. We want these calls to run on the logged-in Claude subscription.
- **Verify every flag** against `claude --help` on the installed version before the first real call:
  - `--max-turns` in print mode, `--setting-sources ""` and `--tools` are version-dependent. Drop or replace any flag that doesn't exist.
  - If `--json-schema` is unavailable, fall back to parsing JSON out of `result`.
  - Record the exact final command line in `results/REPORT.md`.
- **Check isolation once.** In the smoke call, ask "List any tools or skills you have". Confirm it reports none. Record the envelope's input token count as the per-call overhead.
- **Prompt:** identical for all LLMs. It contains the task instructions, the option list with the *same* descriptions Laya gets, and the record's `state`.
- **Parsing:** read `structured_output`, falling back to `result`. On malformed output, retry once, then record `label=null`, which counts as an abstain, not as wrong.
- **Record** `total_cost_usd`, `duration_ms` and the resolved model id from the envelope. On a Pro/Max login, `total_cost_usd` is **notional**: usage is included in the subscription. Report it as "API-equivalent cost", which is the right number for a would-this-be-cheaper comparison.
- **Latency for Claude:** report the envelope's `duration_ms`. Don't use wall-clock, which includes CLI startup; report that separately as "CLI overhead".
- **Call budget:**
  - `haiku` and `sonnet` run on every gold test item of every task.
  - `opus` runs on a stratified sample of **≤150 test items per task**. That keeps subscription usage sane. Report its confidence intervals accordingly.
- **Concurrency:** at most 4 parallel calls. Back off exponentially on rate-limit errors (see S2).

## Phase 4 — Metrics

Compute on the **gold test split**, per task and per system:

- Accuracy and macro-F1, with **95% bootstrap CIs** (1,000 resamples).
- **ECE** (15 bins) and Brier score, for systems that output probabilities. For Claude, use its self-reported confidence and label it as such.
- **The headline metric: coverage at the required precision.**
  - Sort by confidence, descending. Find the largest prefix whose precision is at least the task's required precision.
  - Also report the Wilson 95% lower bound on precision at that point.
  - The threshold is chosen on `cal` and *applied* to `test`. Never tune it on test.
  - For T2, only the non-dismiss actions count toward coverage.
- **Option-order flip rate** from `laya0-perm`.
- **Cost:**
  - Latency p50 and p95 per item.
  - `$` per 1k decisions. Laya counts as $0 marginal; Claude uses the reported `total_cost_usd`.
  - Peak RSS for local systems.

## Phase 5 — Decision rules: is it worth it?

A task is **ADOPT** when the best Laya variant (`layaT` or `laya-head`) meets **all** of these on gold test:

1. **Coverage** at the required precision is **≥ 30%**, and the precision's Wilson lower bound is **≥ required precision − 0.05**.
2. That coverage beats the `heuristic` baseline's coverage at the same precision by at least 10 points (absolute), or no heuristic exists.
3. p50 latency on the MacBook is at most 1,000 ms per decision, and peak RSS is at most 3 GB.
4. Its test set has at least 40 gold items (otherwise it is exploratory, per S3).

**Interpreting the result against Claude:**

- If Laya's coverage is within 10 points of `haiku`'s, Laya wins outright: free, local and private.
- If `haiku` beats it by more than 10 points, still ADOPT when rules 1–3 hold. In that case Laya is a **pre-filter** that removes the easy share before Claude sees the rest. The report must say so plainly.
- If no Laya variant passes rule 1 but a Claude model does, the verdict is **DROP (Laya)**. Note which Claude model would be the cheaper tier for that task instead. This is useful regardless.

**Verdict labels:**

- `ADOPT (zero-shot + calibration)`
- `ADOPT (trained head)`
- `EXPLORATORY`
- `DROP`

Each verdict carries a one-sentence reason and the numbers behind it.

**Overall:** "Laya is worth it" means at least one task is ADOPT. If none is, the report says so in its first line, and Phase 6 is skipped.

## Phase 6 — Implement the winners (shadow mode only)

Do this only for ADOPT tasks.

### 1. Serve Laya locally, and only on demand

- Add a `claude-acs laya` subcommand to this setup, following the pattern of the other `claude-acs` subcommands in the README's "What's running" table.
- It starts `laya-serve` on `127.0.0.1:8765`, not `0.0.0.0`, with these settings:
  - `LAYA_MODELS=english`
  - `LAYA_PRELOAD=1`
  - `LAYA_THREADS=<perf cores>`
  - `LAYA_DEVICE=<chosen>`
  - `LAYA_API_KEY` from the macOS Keychain. It is never committed.
- **Do not install a launchd job.** Because of the memory constraint, it runs only while a triage or vault job needs it. Callers start it, then stop it when they finish.

### 2. Ship the calibration and heads as artefacts

- The fitted temperatures and any trained heads go to `~/.local/share/acs-laya/` with a `manifest.json`. That file records the dataset card hashes, the fit date and the thresholds.
- These are *derived from private data*, so they are **not** committed. The setup README says how to regenerate them: `run.py --fit-only`.

### 3. Wire the winners in, in shadow mode

Each caller asks Laya first and **always** continues down its existing path. It appends one line per decision to a shadow log: `~/.cache/acs-laya-bench/shadow/<task>.jsonl`, holding the id, Laya's label, the probability, whether it would have auto-acted, and the actual final decision.

| Task | Integration point |
|---|---|
| T1 | The bulk duplicate matcher (per its spec). Add a Laya score beside the Jaccard score, in the same band report. |
| T2 | The tide-triage agent's pre-pass. Record a Laya pre-classification the agent can see as a hint. It is **never** used for `dismiss`. |
| T3 / T4 | Wherever the triage or handbook writer assigns `type` or `category`. |
| T5 | `related_suggest.py`. Add a Laya relevance column beside the additive score. |
| T6 | `vault_audit.py`. Add a Laya probability beside `is_placeholder`. |

- Every integration is **off unless** `ACS_LAYA=shadow`. Default it to `shadow` in the scheduled-run environment.
- The report documents `ACS_LAYA=active` as the promotion switch, and states it may only be flipped after two weeks of shadow logs. `bench/shadow_report.py` must then show:
  - agreement with the final decision at least at the required precision, over at least 50 would-have-acted items;
  - zero would-have-auto-dismissed items.
- **Check the callers' own tests and style before editing.** If a caller isn't in a repo you can reach, or changing it would widen scope beyond a hook point, write the patch to `setups/laya-decision-benchmark/patches/<task>.md` instead. Say so in the report.

## Phase 7 — Report and wrap-up

**1. Write `results/REPORT.md`** (aggregates only). Its sections, in order:

- **The verdict in the first three lines:** worth it or not, and for which tasks.
- The verdict table: one row per task, with the verdict, best Laya variant, coverage at the required precision (with its lower bound), and the same for heuristic, haiku, sonnet and opus.
- A per-task metrics table with confidence intervals.
- Cost and latency: Laya on the Mac against each Claude model, per 1,000 decisions.
- Calibration: `laya0` against `layaT` ECE.
- The flip rate.
- Surprises and caveats, including every place silver labels were used.
- What was not run, and why.
- The exact commands, versions and machine specs.
- What's integrated in shadow mode, and how to promote it to active.

**2. Render a local HTML report** to `~/.cache/acs-laya-bench/report.html`. It holds the same content plus precision–coverage curves per task. Open it on the Mac with `open` at the end.

**3. Update this note.**

- Append a short **Results** section with a link to `REPORT.md`.
- Set **Status** to `concluded → promoted to setups/laya-decision-benchmark` if anything was adopted, or `concluded → dead end` with the reason otherwise.
- Update the row in `research/INDEX.md`.

**4. Add a `CHANGELOG.md` entry** that matches the existing style: dated, and led by the verdict in bold.

**5. Commit, open the PR, and merge it,** as described in the Git section above.

**6. Final chat message to Alan.** Include:

- the verdict table;
- the three most important numbers;
- the shadow-mode switch;
- links to the merged PRs;
- where the HTML report is.

Keep it to one screen.

---

## Risks and gotchas to keep in mind

- **Circular grading.** The silver labels came from Claude, so Claude models will look better on silver. That's why verdicts use gold only.
- **Label leakage in T3.** The folder path and frontmatter reveal the type. Strip both.
- **Token budget.** The English checkpoint has about 320 tokens of state. Long signals get truncated silently unless you truncate them deliberately and log it.
- **The confidence field.** Gate on `answer_confidence` or on the calibrated probabilities. `confidence` is entropy-based and is not comparable across option counts.
- **Thread pinning.** Unpinned torch threads make CPU inference more than 10× slower. Pin them in every entry point, including `laya-serve` (`LAYA_THREADS`).
- **Pin the version.** Laya ships often: v0.3.20 came 8 days after release. Keep `laya==0.3.20` for this run so the results stay reproducible, and treat an upgrade as a re-run.
- **Small gold sets.** Around 90 duplicate mappings means wide confidence intervals. Report them, and don't over-claim.
- **Not clinical.** Nothing here evaluates or authorises Laya for clinical decisions. It has no medical evaluation, and the vault's clinical-adjacent content passes through the PHI gate only to be excluded.

## Sources

- Laya repo, README and BENCHMARKS (v0.3.20): https://github.com/NandhaKishorM/laya
- Node/ONNX client: https://github.com/receptron/laya
- Apple-silicon runtime (community): https://github.com/tc3oliver/laya-apple
- Frozen-encoder heads (community): https://github.com/bladedevoff/stuntd
- Jev announcement: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Jev API: https://jevmodel.org/api/
- Independent benchmarks: https://github.com/AbdelStark/jev-benchmarks and https://github.com/nibzard/decision-model-benchmark
- Internal:
  - `acs-anamnesis/Projects/acs-task-manager/specs/2026-08-14-tide-triage-agent-design.md`
  - `acs-anamnesis/Projects/acs-task-manager/specs/2026-08-26-bulk-duplicate-matcher-design.md`
  - `acs-anamnesis/Projects/acs-task-manager/triage-agent/decisions-log.md`
  - `acs-anamnesis/Projects/claude-skills/vault-related/`
  - `acs-anamnesis/Projects/claude-skills/vault-maintenance/`

## Results

_Not yet run._
