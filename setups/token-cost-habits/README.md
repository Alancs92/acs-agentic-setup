# Token-cost habits (ccusage + caveman)

- **Created:** 2026-07-19
- **Status:** active
- **Agents involved:** Claude Code
- **Environment:** local (runs on whatever machine actually runs Claude Code day-to-day — not this docs repo's own sessions)
- **Topology:** single agent

## What this is

The "adopt now, no real downside" half of the token-cost research in
[`../../research/claude-code-token-cost-tools.md`](../../research/claude-code-token-cost-tools.md):
a measurement tool (`ccusage`) plus a situational output-compression skill
(`caveman`). Both verified legitimate; expectations calibrated to
independently-reproduced real-world numbers, not marketing claims.

## Why

Token spend is only manageable if it's visible, and cheap wins (an
always-on measurement tool, a skill with zero execution risk) are worth
adopting outright rather than staying in "research" limbo. `headroom` is
deliberately **not** included here — see
[`../../research/usage-patterns.md`](../../research/usage-patterns.md) for
why that one depends on actually observing usage first.

## Components

- **ccusage** — read-only local CLI, parses Claude Code session logs into
  usage reports. No proxying, no network interception. Correct repo:
  `https://github.com/ccusage/ccusage` (the original token-cost article
  cited a stale owner).
- **caveman** — Claude Code skill, system-prompt-level only (no code
  execution, no proxying). Forces terse output on request.

## Reproducing it

**ccusage** — measure, don't install-and-forget:

```bash
npx ccusage@latest
```

- Add it to the Claude Code statusline so the number is ambient instead of
  requiring a conscious check (see `ccusage`'s own docs for the statusline
  integration — this note doesn't duplicate install docs that change
  upstream).
- **Check cadence:** not daily. Check after any session that felt
  long/expensive, plus roughly once a week for trend awareness. Checking a
  bill you can't change day-to-day isn't worth a daily habit.
- **What to actually look at:** the `cache_create` vs. `output` vs. `input`
  split, not the total. The independently-reproduced replay data in the
  research note found real bills are dominated by `cache_create` (~42%) and
  output (~29%) — so a high `cache_create` share points at context-churn /
  `/clear`-discipline problems, which no compression tool here fixes.

**caveman** — install via the upstream script, toggle situationally:

```bash
curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash
```

- Toggle on (`/caveman`) for verbose/explanatory-heavy sessions.
- Leave off for pure code-generation work — independently-reproduced data
  shows it can go net-negative there (the skill itself costs ~1–1.5k input
  tokens per turn, and pure-codegen sessions don't have much verbose output
  to trim).
- **Expected real impact:** ~4–10% of total session cost on mixed
  workflows, not the marketed 65% (that number is real but applies only to
  output tokens, the cheapest part of the bill).

Before running the `curl | bash` installer, read `install.sh` first (or
install manually by copying the skill files) — standard practice for any
pipe-to-shell installer regardless of how well-vetted the source repo is.

## Config

No secrets involved. `ccusage` reads local JSONL session logs only.
`caveman` is a skill/prompt addition, no config file beyond whatever the
installer drops into `.claude/skills/`.

## Notes / known issues

- Neither tool has been live-installed from *this* repo's sessions — this
  repo is documentation-only; the environment that actually runs these day
  to day is wherever Claude Code is used for real work. The date at the top
  of this file is when it was documented, not when either tool was
  installed.
- Revisit the `caveman` net-impact numbers periodically — independent
  benchmarks noted variance (22–87% range on output tokens specifically),
  so mileage will vary by workload.
