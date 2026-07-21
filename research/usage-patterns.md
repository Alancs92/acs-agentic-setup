# Alan's Claude Code usage patterns

- **Date:** 2026-07-19 (started; meant to accumulate over time)
- **Status:** open — living note, not a one-time conclusion

## Question

Two purposes:

1. Build an actual picture of how Claude Code gets used day to day, instead
   of guessing — so tooling decisions (like the `headroom` question below)
   are based on real sessions, not vibes.
2. Specifically: is [`headroom`](https://github.com/headroomlabs-ai/headroom)
   (verified legitimate in
   [`claude-code-token-cost-tools.md`](claude-code-token-cost-tools.md), but
   only clearly worth its proxy-mode complexity for RAG-heavy or
   huge-log/JSON-heavy workloads) actually a fit?

## What's known so far

- **Workflow shape:** a lot of docs-based development — sessions that read,
  cross-reference, and update documentation frequently, not primarily
  code-generation. (Reported 2026-07-19; not yet measured.)
- **Open question this raises:** "checks docs a lot" could mean two very
  different things for token cost, and they call for different fixes:
  1. **Re-reading the same docs repeatedly across a session** (context
     churn) — this shows up as a high `cache_create` share in `ccusage`
     (see [`../setups/token-cost-habits/README.md`](../setups/token-cost-habits/README.md)).
     Headroom doesn't fix this. The fix is context hygiene: better
     `INDEX.md`-style navigation (which this repo already leans on — see
     [`../README.md`](../README.md)'s navigation contract), more aggressive
     `/clear` between unrelated doc sections, or a local knowledge-graph
     tool (`graphify` was flagged **unverified/high-risk** in the original
     research pass — would need its own deep-dive before trusting it, the
     way `headroom`/`caveman` got one).
  2. **Pulling large chunks of doc/log/tool-output content into context
     per-turn** (e.g. big file reads, grep dumps, tool results) — this is
     exactly what `headroom` targets (70–90% reduction on structured/large
     content specifically), and would actually justify its proxy-mode setup
     complexity.

## What to do next (for whichever agent picks this up, local or otherwise)

This note is intentionally left with concrete, checkable next steps rather
than a guess:

1. Run `ccusage` (or `ccusage blocks` / session breakdown — check current
   `ccusage` docs for the exact subcommand) on a handful of real recent
   doc-heavy sessions.
2. Look at the `cache_create` vs. `output` vs. `input` split per session.
   - High `cache_create` → pattern (1) above → don't bother with
     `headroom`; fix context hygiene instead.
   - High input from large tool-result payloads (big file reads, grep/log
     dumps) → pattern (2) → `headroom` in library or MCP-server mode
     (not full proxy) is worth trying, per the recommendation in
     [`claude-code-token-cost-tools.md`](claude-code-token-cost-tools.md#update-2026-07-19-deep-dive-on-headroom-caveman-karpathy-skills).
3. Record the finding back in this file (append, don't overwrite — this is
   meant to build a track record over multiple observations, not just the
   latest one).
4. If a pattern holds across several sessions (not just one data point),
   promote the conclusion into `setups/token-cost-habits/` (or a new
   `setups/headroom-*` entry if headroom turns out to be worth adopting).

## Findings log

_(Append dated entries here as they come in. Format: date, what was
observed, what it implies.)_

- **2026-07-19:** Initial note created from a stated workflow description
  (docs-based development, frequent doc-checking) — not yet backed by
  `ccusage` data. First real entry should be a `cache_create`/`output`
  breakdown from actual sessions.

## Conclusion / next step

Not concluded — this is explicitly left open pending real `ccusage` data
from actual doc-heavy sessions, rather than deciding on `headroom` from a
workflow description alone.
