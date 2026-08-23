<!--
This specific investigation. Concludes into a setup (claude-code-local-cli)
once the lean profile is validated. Methodology lives in the sibling note
baseline-metric-methodology.md — don't duplicate it here.
-->

# Token-usage audit — Claude Code local CLI (plugin pruning)

- **Date:** 2026-07-24
- **Status:** open (lean profile applied + measured: −8.7k/−20% startup tax via System tools; skills curation deprioritised as low-yield; next: per-account setup mechanism)

## Question

What inflates the per-turn context tax in the local Claude Code setup, and can
we cut it — chiefly by pruning always-on plugins to a lean core, enabling the
rest per-project — **without** losing capabilities we actually use? Measured,
not eyeballed. See [`baseline-metric-methodology.md`](baseline-metric-methodology.md)
for the how-to-measure protocol this audit follows.

## Findings

### What drives the tax (ranked)

1. **Plugin breadth (dominant lever).** 26 always-on plugins in the personal
   profile. Every session loads each plugin's skills (name + description) and,
   for the ~10 MCP-bearing ones, their deferred tool *names* — a long manifest
   paid every turn whether or not that day's work touches figma/vercel/etc.
2. **claude.ai account MCP connectors** (Gmail, Calendar, Canva, Coupler,
   PubMed, Indeed…) add dozens of tool-name lines. Disconnected in claude.ai
   web settings, not in a local file. Medium lever.
3. **User skills** — 70 user skills. Cheap-ish (one line each) and high personal
   value (briefings, timesheet, textx). Not a primary target; audit only for
   dead *families*.
4. **Duplicate CLAUDE.md** — `~/.claude-accounts/personal/CLAUDE.md` and
   `~/.claude/CLAUDE.md` are byte-identical (2796 B, 51 lines), loaded per
   session. **Intentionally kept** — they are separate work/personal accounts
   that may legitimately diverge. ~700-token cost accepted.

### Measured reality (baseline 2026-07-24) — REVISES the ranking above

Captured via `/context`, fresh session, Sonnet 5, 26-plugin full profile.
Header **46.5k / 967k (5%)**. Startup tax (all categories except Messages) ≈ **42.4k**:

| Category | Tokens | Plugin-addressable? |
|---|---|---|
| System tools | 17.1k | No — built-in tool schemas, fixed |
| System prompt | 10.6k | No — fixed |
| **Skills (219)** | **9.9k** | **Yes — main lever** (plugins add ~149 of 219) |
| Memory files (3) | 3.4k | No — cwd-dependent (CLAUDE.md etc.) |
| **Custom agents (11)** | **1.4k** | **Partly** (feature-dev, code-review, vercel, agent-sdk-dev…) |
| **MCP tools (131)** | **0** | **No — deferred, 0 active tokens** |

**Thesis reversal:** MCP tool schemas are **deferred to 0 active tokens**, so
pruning MCP-bearing plugins (figma/vercel/cloudflare/supabase/notion…) saves
essentially nothing in active context — the opposite of the original #1/#2
findings. The only plugin-addressable token cost is **Skills (9.9k)** + a slice
of **Custom agents (1.4k)**. Realistic saving from the lean profile: a few k
tokens — low single-digit % of a 200k (Opus) window, <0.5% of Sonnet's 967k.

**Implication:** the case for the lean profile is now mostly **decision/focus
overhead** (fewer skills/agents for the model to weigh), not raw token savings.
If *tokens* are the goal, the higher-leverage target is the **Skills row
directly** (curate 219 skills) rather than pruning MCP plugins. This is exactly
the kind of course-correction the baseline was meant to surface.

### The lean-profile decision

- **Always-on core (18):** superpowers, feature-dev, code-review,
  code-simplifier, claude-md-management, claude-code-setup, skill-creator,
  security-guidance, explanatory-output-style, github, context7, qmd, slack,
  typescript-lsp, pyright-lsp (Python), gopls-lsp (Go), hai-agentic-dev,
  obsidian.
- **Move to per-project (enable on demand):** agent-sdk-dev, figma, playwright,
  supabase, vercel, cloudflare, frontend-design, ralph-loop, csharp-lsp,
  rust-analyzer-lsp, greptile, notion.
- **Rationale notes:**
  - LSPs are cheap (one server, only active in that language's repos) — TS +
    Python + Go always-on is low cost; C#/Rust per-project.
  - **slack** is core because the morning-briefing / EOD skills use it.
  - **notion, greptile** confirmed per-project (not daily).
  - **ralph-loop** is per-project and a prime *drop* candidate: `/loop` (a
    time-interval scheduler) does NOT replace it (a state-driven
    iterate-until-green convergence loop), but superpowers already covers most
    iterate-to-green work (test-driven-development + subagent-driven-development
    + executing-plans). Validate against the metrics before deleting.

## Baseline Record: personal profile — full set (BEFORE)

- **Baseline ID / version:** cc-plugins-full · v1 · 2026-07-24
- **Status:** active
- **Environment / provenance:**
  - Claude Code version: **2.1.218**
  - Exact model ID (capture): **`claude-sonnet-5` (Sonnet 5)**, context window
    **967k**. NOTE: the fresh session defaulted to Sonnet 5, not Opus 4.8 — the
    **AFTER capture must also be on Sonnet 5** or the delta is invalid.
  - OS / machine: macOS (Darwin 25.5.0)
  - Config hash (SHA-256 of resolved `enabledPlugins`, personal profile):
    **`eb37db00d6ef48e01012291a82d8f5bc1282f6da3ab29dd57e605e7f0135a817`**
  - Enabled plugins: **26** (list above / in `.claude-accounts/personal/settings.json`)
  - User skills: 70 · total skills loaded: **219** · custom agents: **11** ·
    project `.mcp.json`: empty (MCP via plugins + claude.ai connectors)
  - Tool Search / deferred tools: ON (default) — hold constant for the AFTER run.
- **Primary metric — startup context tokens (`/context`), captured 2026-07-24:**
  - Header total: **46.5k / 967k (5%)** (incl. Messages 4.1k).
  - **Startup tax (excl. Messages): ≈ 42.4k** —
    System tools 17.1k · System prompt 10.6k · Skills 9.9k (219) ·
    Memory files 3.4k (3) · Custom agents 1.4k (11).
  - **MCP tools: 131 · 0 tokens (deferred / on-demand).**
  - **Plugin-addressable rows only:** Skills 9.9k + slice of Custom agents 1.4k.
- **Cross-check — first-turn `cache_creation_input_tokens`:** not captured (n/a).
- **Decision rule (OEC):** ship the lean profile only if startup tokens drop
  materially AND task-success + tool-found guardrails stay within noise on a
  fixed suite (per-segment, incl. a task that needs a pruned-adjacent plugin).

### Capture note
`/context` is interactive-only and can't be read non-interactively by the
agent, so the headline was captured by a human-run fresh session (Messages ≈ 0)
rather than faked from the deep working session — keeping the startup tax clean.

## Baseline Record: lean profile (AFTER)

- **Baseline ID / version:** cc-plugins-lean · v1 · 2026-07-24
- **Status:** active (applied 2026-07-24 to `.claude-accounts/personal/settings.json`;
  backup at `settings.json.bak-2026-07-24`)
- **Config hash (SHA-256 of resolved `enabledPlugins`):**
  **`59a9cd9351a982b637b0578a06b29afdb394587df27586cc1fba982738bfbbc5`**
- **Enabled plugins: 18** (removed 12 → per-project: agent-sdk-dev, figma,
  playwright, supabase, vercel, cloudflare, frontend-design, ralph-loop,
  csharp-lsp, rust-analyzer-lsp, greptile, notion; added 4: pyright-lsp,
  gopls-lsp, hai-agentic-dev, obsidian).
- **Same controls as BEFORE:** Sonnet 5, CC 2.1.218, tool-search ON.
- **Primary metric — `/context` startup tokens, captured 2026-07-25:**
  - Header total: **37.8k / 967k (4%)** (incl. Messages 4.1k) — was 46.5k.
  - **Startup tax (excl. Messages): ≈ 33.7k** — was 42.4k → **−8.7k (−20%)**.
  - System tools **10.5k** · System prompt 10.2k · Skills **10.0k (146)** ·
    Memory 2.2k (3) · Custom agents **0.82k (6)**.
  - **MCP tools: 180 · 0 tokens** (count rose from 131, cost still 0 — deferral thesis holds).

### Delta analysis (BEFORE → AFTER) — the REAL mechanism

| Row | Before | After | Δ | Attributable? |
|---|---|---|---|---|
| **System tools** | 17.1k | 10.5k | **−6.6k** | **Yes — dominant.** Pruned plugins' non-MCP tool schemas |
| Custom agents | 1.4k (11) | 0.82k (6) | −0.6k | Yes — removed plugins' agents |
| **Skills** | 9.9k (219) | 10.0k (146) | **+0.1k** | ~flat: 73 fewer skills but remaining ones are heavier (long user-skill triggers) + added obsidian/hai/LSP skills |
| Memory files | 3.4k | 2.2k | −1.2k | **No — confounder** (cwd/auto-memory-index dependent, not the plugin change) |
| System prompt | 10.6k | 10.2k | −0.4k | No — noise |
| MCP tools | 0 (131) | 0 (180) | 0 | No — deferred regardless of count |

**Thesis correction (second one):** the saving came through **System tools
(−6.6k)** — built-in/plugin-registered *tool schemas* — NOT the Skills row and
NOT MCP. Removing playwright/vercel/supabase/figma/etc. dropped their real
(non-deferred) tool schemas. Plugin-attributable saving ≈ **−7.2k** (System
tools + Custom agents); the −1.2k memory drop is a cwd confounder, excluded.
- **Skills row stayed flat** despite 219→146 skills → skill *count* isn't the
  lever; heavy *descriptions* are. So further token gains need trimming the
  fat user-skill trigger blocks, not just deleting skills. Confirms the
  Skills-row curation section below is low-yield (~1.3k ceiling).
- **Net: the lean profile saved ~7k real tokens (~17% of startup tax)** — more
  than the "few k" predicted, but via a row nobody guessed. Vindicates measuring.

## Skills-row curation (2026-07-24)

Skills row = 9.9k tokens / 219 skills. Of that, the **70 user skills ≈ 6.2k**
(frontmatter descriptions; measured) — the bigger remaining lever than the 18
core plugins' skills. Heaviest user skills are long trigger-phrase blocks
(skill-improver 242t, textx-ide-config 205t, textx-checkpoint 199t,
textx-new-ticket 191t, harrison-instructor 177t).

**Provenance matters (curate at the right layer):**
- `skill-creator`, `code-review` (and other bootstrap names: code-commit,
  d2-diagrams, d2-render, git-worktrees, iterative-development, pr-code-review,
  security, session-management, agent-teams) are **symlinks into
  `claude-bootstrap/.claude/skills/`** → curate at the bootstrap source, else
  `bootstrap-sync` restores them.
- Cloudflare family (`cloudflare`, `cloudflare-email-service`, `cloudflare-one`,
  `cloudflare-one-migrations`, `wrangler`, `workers-best-practices`,
  `durable-objects`, `sandbox-sdk`, `agents-sdk`, `web-perf`, `turnstile-spin`,
  ≈ **1.0k tokens**) are **real dirs, not in bootstrap** → removable directly;
  they return via the per-project `cloudflare` plugin when CF work happens.
- textx-*/engineer-*/spark/tracker/timesheet/jira/v2-support/harrison-instructor
  = the personal **workflow moat** — keep.

**Curation candidates (proposed, pending owner decision — not yet actioned):**
| Tier | Skills | ~tokens | Mechanism | Risk |
|---|---|---|---|---|
| 1 dup-of-core-plugin | user `skill-creator`, `code-review` | ~70 | at bootstrap source | low (plugin covers it) — but confirm which copy is canonical |
| 2 Cloudflare family (11) | see list above | ~1000 | delete real dirs; return via per-project plugin | low if CF work is occasional |
| 3 overlapping families | `pr-code-review` vs `pr-review-advisor`; `skill-sync`/`copilot-skill-sync`/`bootstrap-sync` | ~300 | consolidate | med — needs owner judgment |

Net addressable ≈ 1.3–1.5k tokens — modest, consistent with "already lean".

## Conclusion / next step

**Reframed after the baseline:** because MCP tools are already deferred to 0
active tokens, plugin pruning is a **focus/decision-overhead** change, not a
token-savings one. Expected token delta is small (Skills + agents rows only).
Decide whether that's worth it, or pivot the token effort to curating the
**Skills row (219 → fewer)**, which is where the addressable 9.9k actually sits.

1. ~~Capture the BEFORE headline~~ **DONE** (2026-07-24, Sonnet 5) — see Baseline Record.
2. ~~Apply the lean profile~~ **DONE** (2026-07-24) — 18 core, hash `59a9cd93…bbc5`,
   backup `settings.json.bak-2026-07-24`.
3. ~~Capture the AFTER headline~~ **DONE** (2026-07-25, Sonnet 5) — 37.8k total,
   **−8.7k (−20% of startup tax)**, saving via System tools (−6.6k), not Skills/MCP.
4. **Run the guardrail suite** interleaved (methodology Part B) before trusting
   the cut.
5. On success → promote to **`setups/claude-code-local-cli/`** and wire the
   lean⇄full toggle into the existing `claude-acs` account script.
