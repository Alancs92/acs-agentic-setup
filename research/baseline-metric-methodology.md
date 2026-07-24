<!--
Reusable methodology note. Referenced by token-usage-audit.md and any future
before/after measurement of an agentic setup. Keep this one long-lived even
after individual audits conclude.
-->

# Baseline metrics for agentic-setup changes — measurement + methodology

- **Date:** 2026-07-24
- **Status:** open (living reference — not tied to a single audit)

## Question

When we change an agentic setup (e.g. prune enabled Claude Code plugins), how
do we measure the effect in a **trustworthy, reproducible, evidence-based**
way — both the token/context saving and any loss of usefulness — instead of
eyeballing "feels lighter"?

Two halves to answer: (a) *what instrument* actually reads Claude Code's
context/token usage, and (b) *what methodology* makes a before/after
comparison believable.

---

## Part A — How to measure Claude Code token/context usage

*Verified against Claude Code 2.1.218, ccusage 20.0.18, macOS.*

### Method 1 — `/context` (primary instrument for the startup-context tax) ★

- **Measures:** current context-window occupancy, itemised by category —
  System prompt, System tools, MCP tools, Custom agents, Memory files
  (CLAUDE.md project + global + first ~200 lines of the auto-memory index),
  Skills, Messages, Free space. Each row shows tokens + % of window.
- **How:** launch `claude` in the target dir, type `/context` as the **first
  and only** input. With Messages ~0, the sum of the other rows *is* the
  startup context tax as a single number (e.g. header "51k/200k (26%)").
- **Precision:** token-exact per category (actual tokenised sizes, not
  estimates). Introduced v1.0.86.
- **Caveat:** with **Tool Search / deferred tools** on (current default),
  heavy schemas move out of active context and load on demand; `/context`
  reports *active* occupancy plus "deferred" rows. That's the number we
  want — but it's policy-dependent, so hold the tool-search setting constant
  across runs.
- **Limitation for automation:** `/context` is an interactive TUI command —
  it cannot be invoked non-interactively by an agent. The headline number is
  a human-run, fresh-session step.

### Method 2 — OpenTelemetry `claude_code.token.usage` (per-turn cost delta) ★

- **Measures:** a Counter emitted after every API request, attributes
  `type` (input / output / cacheRead / cacheCreation), `model`,
  `query_source` (main / subagent / auxiliary), plus attribution labels
  (`mcp_server.name`, `skill.name`, `agent.name`; redacted unless
  `OTEL_LOG_TOOL_DETAILS=1`). Authoritative (from API response), unlike the
  JSONL numbers.
- **How (local console exporter, no backend):**
  ```bash
  export CLAUDE_CODE_ENABLE_TELEMETRY=1
  export OTEL_METRICS_EXPORTER=console
  export OTEL_METRIC_EXPORT_INTERVAL=1000   # 1s local feedback (default 60000)
  claude
  ```
  Backend: `OTEL_METRICS_EXPORTER=otlp`, OTLP endpoint `:4317`, Prometheus
  scrape `:9464/metrics`. Isolate the first `type=input` + `cacheCreation`
  data point = startup tax as actually billed. Status: **beta**.

### Method 3 — `ccusage` (trend / cache accounting only)

- `npx ccusage@latest session --json` aggregates
  `~/.claude/projects/**/*.jsonl` into daily/session/blocks reports.
- **Do NOT trust input/output totals** — upstream Claude Code JSONL logging
  writes streaming placeholders (`output_tokens` often 1–2; ~75% of
  `input_tokens` are 0/1), undercounting input up to ~100x. **Cache tokens
  (`cache_read_input_tokens`, `cache_creation_input_tokens`) ARE accurate.**
  Use only for cache accounting or trend visualisation.

### Method 4 — raw JSONL parse (reliable cross-check via cache tokens)

- Schema (confirmed): each `type=="assistant"` record has `.message.usage` =
  `{input_tokens, output_tokens, cache_creation_input_tokens,
  cache_read_input_tokens, cache_creation.{ephemeral_1h,ephemeral_5m}_input_tokens, ...}`;
  records also carry `version`, `gitBranch`, `cwd`, `sessionId`, `timestamp`.
- The **`cache_creation_input_tokens` on the first assistant turn** ≈ the
  whole static preamble newly cached — a reliable programmatic cross-check on
  the `/context` startup number.
  ```bash
  F=$(ls -t ~/.claude/projects/<slug>/*.jsonl | head -1)
  jq -c 'select(.type=="assistant") | .message.usage
         | {in:.input_tokens, out:.output_tokens,
            cache_create:.cache_creation_input_tokens,
            cache_read:.cache_read_input_tokens}' "$F" | head -1
  ```

---

## Part B — Methodology for a trustworthy before/after

### Principles

1. **Change exactly one variable.** Baseline and treatment differ only in the
   thing under test; model, prompt, repo state, harness, machine held
   constant. Cross-environment comparison is the #1 benchmarking mistake.
2. **Fix the workload + harness.** One deterministic, pinned task set run
   identically before and after — else you measure the workload, not the change.
3. **Cold vs warm.** The startup-context tax is a *cold, deterministic*
   quantity — a strength: measure once cleanly per config. Warm-up discipline
   (discard early iterations) matters for the *stochastic guardrail* runs.
4. **Repeat — never one number.** Deterministic quantity (startup tokens): 1–3
   confirmatory runs. Stochastic quantity (task success): 5+ floor, 20–30
   rigorous, in freshly-spawned sessions.
5. **Report central tendency AND spread** (median + variance/CI; p95/p99 for
   tails). A single number with no spread is untrustworthy.
6. **Pin + hash the environment.** Record Claude Code version, exact model ID,
   and a **SHA-256 of the resolved enabled-plugin manifest**. Any config/prompt
   change → new hash → drift is detectable and a stale comparison is caught.

### Metric design — primary + guardrails

- **Primary (optimise):** startup/context tokens from the enabled set
  (tool schemas + skill/plugin defs + MCP tool names + CLAUDE.md). Deterministic,
  cheap, token-exact via `/context`.
- **Guardrails (protect the real goal):**
  - Task success rate on a fixed representative suite.
  - Tool-availability / retrieval: for tasks needing a pruned-adjacent
    capability, did the needed tool still get found/invoked.
  - Turns / cost to completion (extra retries after a helper was removed = regression).
- **Goodhart guard:** token count is a *proxy* — driven alone it goes to zero
  by deleting everything, destroying usefulness. Pair opposing indicators
  (tokens saved ↔ task success) as an OEC, and watch **per-segment** effects
  (an average can hold while the one task category that used the pruned plugin
  collapses — Simpson's-paradox risk).

### Before/after vs A/B

- **Deterministic primary (startup tokens):** simple before/after is valid *if
  the environment is fully pinned + hashed*. before − after = the causal effect.
- **Stochastic guardrails:** need a controlled comparison. Cheapest valid
  design = run baseline and treatment **interleaved** over the same suite in the
  same session window (paired/randomised ordering), not "all-before-yesterday,
  all-after-today" — that controls for model-version/time confounds. A model or
  CC version bump between captures invalidates the comparison (hence the hash).

### Reusable baseline-record template

```
# Baseline Record: <name>
- Baseline ID / version:   semver + date
- Status:                  active | superseded-by <id>
## Environment / provenance
- Claude Code version:
- Exact model ID:
- OS / machine:
- Config hash (SHA-256 of resolved enabled-plugin manifest):
- Full enabled plugin/skill/MCP list (or pointer to hashed manifest)
## Workload / harness
- Task suite (pinned IDs), warm-up policy, runs per condition, ordering
## Metrics
- Primary (definition + how counted), guardrails, OEC / decision rule
## Raw results
- Per-run raw numbers (not just summaries), median + variance/CI, per-segment
## Methodology notes / caveats / confounders
## Reproduce command
```

Store raw per-run numbers, not only summaries. Superseding a baseline = a new
version with a new hash (append-only), so drift stays visible.

### Pitfalls checklist

- [ ] Non-reproducible (no version / model / config hash recorded).
- [ ] Moving target (workload/model/repo changed between before and after).
- [ ] Proxy trap (optimise tokens, silently lose capability — no guardrail defined).
- [ ] Insufficient runs (single stochastic run; gain < run-to-run noise).
- [ ] Ignoring variance (one number, no spread).
- [ ] Average hides the tail / one collapsed segment.
- [ ] Cold-start contamination (warm-up transients in measured data).
- [ ] Cross-environment comparison.
- [ ] Naive before/after where a control/interleave was needed.

---

## Recommended protocol for the plugin-pruning audit

Two coupled measurements:

**1. Token metric (deterministic before/after):**
1. Record env: `claude --version`, model, plugin set, MCP servers, skills.
2. **BEFORE:** fresh `claude` in target dir → type `/context` first → record
   header total + per-category (esp. MCP tools, Skills, System tools).
3. *(cross-check)* first-turn `cache_creation_input_tokens` via Method 4.
4. Apply the change (prune plugins). **Fully quit + relaunch** (a live session
   won't reload plugins/MCP).
5. **AFTER:** repeat step 2 identically.
6. Delta = before − after, headline = `/context` total + MCP/Skills category deltas.

**2. Usefulness guardrails (interleaved A/B):** fixed task suite exercising
both retained and pruned-adjacent capabilities, runs interleaved/randomised,
report success % + tool-found % + turns, per-segment. **Ship only when the
token reduction is real AND every guardrail stays within noise.**

**Controls held constant:** CC version (pin — don't span an auto-update),
model, cwd, tool-search/deferred-tools setting, SessionStart hooks (this env
has active hooks that can inject variable context — freeze them).

---

## Sources

- Claude Code — Monitoring usage (OTel metrics/env vars): https://code.claude.com/docs/en/monitoring-usage
- `/context` command explainer: https://www.jdhodges.com/blog/claude-code-context-slash-command-token-usage/
- ccusage JSON output: https://ccusage.com/guide/json-output — reliability caveat: https://github.com/ryoppippi/ccusage/issues/866
- Claude Code JSONL token-undercount bugs: #22686, #25941, #27361 (github.com/anthropics/claude-code); https://gille.ai/en/blog/claude-code-jsonl-logs-undercount-tokens/
- Per-version system prompts/tool schemas: https://github.com/Piebald-AI/claude-code-system-prompts
- OTel stacks: https://signoz.io/docs/claude-code-monitoring/ ; https://github.com/ColeMurray/claude-code-otel
- Google SRE Book — SLOs (percentiles, work-backward-from-users): https://sre.google/sre-book/service-level-objectives/
- Robust benchmarking in noisy environments (arXiv 1608.04295): https://arxiv.org/pdf/1608.04295
- Benchmarking best practices: https://www.principledtechnologies.com/benchmarkxprt/blog/2023/05/11/best-practices-in-benchmarking/ ; https://docs.opensearch.org/latest/benchmark/user-guide/optimizing-benchmarks/performance-testing-best-practices/
- Goodhart / guardrails / OEC: https://www.growthbook.io/blog/goodharts-law-and-the-dangers-of-metric-selection-with-a-b-testing ; Kohavi *Trustworthy Online Controlled Experiments* (summary): https://www.luckybookshelf.com/trustworthy-online-controlled-experiments-by-kohavi-tang-xu/
- Quasi-experimental before/after (confounders): https://academic.oup.com/ije/article/52/5/1522/7110226
