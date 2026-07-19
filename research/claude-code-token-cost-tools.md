# Claude Code token-cost reduction stack (2026 "viral Reddit" tools)

- **Date:** 2026-07-19 (updated same day after a deeper follow-up pass)
- **Status:** concluded → see [Update: deep dive](#update-2026-07-19-deep-dive-on-headroom-caveman-karpathy-skills) below for the final verdict and setup recommendation

## Question

A Medium post ("Cut Claude Code Token Costs by 60–90% with Free Open-Source
Tools (Updated July 2026)", santoshyadav979439, source citing a viral June
2026 r/ClaudeAI thread) recommends a stack of open-source tools claimed to
cut Claude Code token spend 60–90%: `ccusage`, `rtk`, `caveman`,
`andrej-karpathy-skills`, `graphify`, `obsidian-skills`, `pxpipe`,
`headroom`. Question: are these tools real, safe, and worth adopting for
this setup?

## The article's claims

| # | Tool | Claimed repo | Claimed stars | What it's supposed to do |
|---|---|---|---|---|
| 1 | ccusage | `github.com/ryoppippi/ccusage` | ~17k | Local CLI, parses Claude Code session logs into usage reports |
| 2 | RTK (Rust Token Killer) | `github.com/rtk-ai/rtk` | ~70k | Proxy/hook that compresses shell command output before it hits the model (claimed 60–90%) |
| 3 | Caveman | `github.com/JuliusBrussee/caveman` | "very high" | Skill that forces terse replies (claimed ~65% output token cut) |
| 4 | Karpathy Skills | `github.com/multica-ai/andrej-karpathy-skills` | ~190k | A CLAUDE.md of coding principles "derived from" Andrej Karpathy |
| 5 | Graphify | `github.com/Graphify-Labs/graphify` | ~80k+ | Local knowledge graph of the codebase so Claude queries it instead of re-reading files (claimed up to 71× fewer tokens/query) |
| 6 | Obsidian Skills | `github.com/kepano/obsidian-skills` | ~41k | Teaches the agent to work with Obsidian vaults |
| — | pxpipe | `github.com/teamchong/pxpipe` | ~5.6k | Local proxy that renders bulky context as PNG images, has Claude read them via vision (claimed 59–70% end-to-end bill reduction) |
| — | Headroom | `github.com/chopratejas/headroom` | (unstated) | Content-aware compressor for tool outputs/logs (claimed 60–95%) |

Article also recommends: `/clear` aggressively between tasks, lean/progressive
CLAUDE.md files, routing simple work to cheaper models, and ending sessions
with a handoff note. That general advice is sound and independent of any of
the tools above.

## Verification (GitHub API + README fetches, 2026-07-19)

| Tool | Repo exists? | Actual data | Assessment |
|---|---|---|---|
| ccusage | **Yes, but wrong owner** | Real repo is `ccusage/ccusage` (17,285 stars, created 2025-05-29 — 14 months of organic-looking growth). `ryoppippi/ccusage` does not resolve; likely the original creator before an org transfer. | **Legitimate.** Read-only local JSONL log parser, no network proxying. Correct the URL before using. |
| obsidian-skills | Yes | `kepano/obsidian-skills`, 42,565 stars, created 2026-01-02. Owner `kepano` = Steph Ango, a real, verifiable Obsidian team member with other credible repos (`defuddle`, 8.5k stars; `flexoki`, 3.6k stars). | **Likely legitimate.** Star velocity is still fast but the maintainer identity checks out. |
| RTK | Yes | `rtk-ai/rtk`, 71,770 stars, created 2026-01-22 (~6 months old). README names real people (Patrick Szymkowiak + 3 others) with public profiles. | **Exists, named humans, but red flags:** 71k stars in 6 months for a Rust CLI is extraordinary; it's a proxy that intercepts shell command output before it reaches the model. |
| Caveman | Yes | `JuliusBrussee/caveman`, 90,619 stars, created 2026-04-04 (~3.5 months old). | **Exists, low technical risk** (it's a prompt-level skill, not code execution/proxying) but the star count is another instance of the same implausible velocity. |
| Karpathy Skills | Yes | `multica-ai/andrej-karpathy-skills`, 194,097 stars, created 2026-01-27. Org bio: "Your next 10 hires won't be human" — a commercial agent-automation company, not Karpathy. | **Exists, biggest red flag.** 194k stars for a single CLAUDE.md file in <6 months exceeds nearly every legitimate repo on GitHub regardless of age. Trades on Karpathy's name without his involvement. |
| Graphify | Yes | `Graphify-Labs/graphify`, 91,156 stars, created 2026-04-03 (~3.5 months old). Installed via `uv tool install graphifyy` (note the double "y" — differs from the repo name `graphify`, worth double-checking before running to rule out a typosquat). | **Exists, same star-velocity pattern.** Executes local analysis (tree-sitter + optional LLM calls), moderate risk. |
| pxpipe | Yes | `teamchong/pxpipe`, 6,475 stars, created 2026-05-20 (~2 months old). README states most commits/docs were "authored by Opus/Fable agent sessions running behind pxpipe itself" — no named human maintainer surfaced, no independent audit. | **Highest risk.** It's a proxy for the *entire* Claude API connection (`ANTHROPIC_BASE_URL` points at it) — every request and response passes through unaudited third-party code. |
| Headroom | **Corrected below** ⚠️ | See [Update](#update-2026-07-19-deep-dive-on-headroom-caveman-karpathy-skills) — this "does not exist" conclusion was **wrong**, caused by trusting the article's stale owner without checking for a renamed/moved repo. | The real repo is `headroomlabs-ai/headroom`; it's legitimate. Original row preserved below for an honest record of the mistake. |

## Assessment

This reads less like six independently-vetted tools and more like a
cross-referencing cluster: new accounts, repos created within the last
2–6 months, implausible star velocity (tens of thousands to ~194k stars in
months — territory that even legendary long-running projects rarely reach),
a "viral Reddit thread" origin story, and — most damning — a recommended
"flagship" repo (`chopratejas/headroom`) that doesn't actually exist while
other repos treat it as real. Two of the tools (`rtk`, `pxpipe`) are also
**proxies that sit in the middle of live Claude API traffic or shell
output**, which is exactly the kind of software where a fake-credibility /
supply-chain-priming campaign would do real damage (credential exfiltration,
prompt injection, tampered output) if or when it turns malicious.

This matches a known pattern: build fake social proof (inflated stars,
interlocking repos, a viral-origin myth) to get developers to pipe live
credentials/traffic through unaudited local proxies.

None of this proves malicious intent — it's possible some of these are
just enthusiastic, badly-marketed, real tools that got star-bombed by bots
for SEO reasons unrelated to the maintainers. But the risk profile (unaudited
proxy + implausible growth + a nonexistent "flagship" repo) is enough to
not adopt the higher-risk tools without a lot more scrutiny.

## Conclusion / next step

**Adopt now (low risk, verified legitimate):**
- `ccusage` — via the correct URL, `https://github.com/ccusage/ccusage`. Read-only,
  measure-first tooling; no reason not to use it.
- `kepano/obsidian-skills` — legitimate maintainer, low execution risk. Worth
  trying since Obsidian vaults are already part of how this repo's
  conventions work (see [`../templates/`](../templates/INDEX.md) pattern).

**Hold off (needs more scrutiny before any setup):**
- `rtk`, `pxpipe` — do not route live API traffic or shell output through
  either without reading the actual source (not just the README), checking
  package-registry publish history, and waiting to see if independent
  security write-ups surface. `pxpipe` in particular: no named human
  maintainer, and it is a full API-traffic proxy.
- `caveman`, `graphify`, `andrej-karpathy-skills` — lower execution risk
  (skills/local analysis, not proxies) but still carry the same
  star-inflation signature. Fine to read the actual CLAUDE.md/skill content
  and adapt what's genuinely useful by hand, rather than installing the
  package/skill wholesale.
- `headroom` — do not use. The specific repo the article names doesn't exist.

**Not done:** no setup has been started for any of these. If `ccusage` gets
adopted, document it as `setups/ccusage-usage-tracking` (or folded into an
existing setup) with the corrected repo URL.

---

## Update 2026-07-19: deep dive on headroom, caveman, Karpathy skills

Follow-up requested after finding that `github.com/headroomlabs-ai/headroom`
resolves — directly contradicting the "does not exist" conclusion above.
Investigated further with GitHub API metadata, README/source fetches, and
web search for independent (non-repo-owned) coverage of `headroom`,
`caveman`, and `andrej-karpathy-skills` specifically.

### Correction: headroom is real

The first pass searched only `chopratejas/headroom` (the owner the article
cited) and stopped at "not found." The actual repo, `headroomlabs-ai/headroom`,
lists **chopratejas as the primary author** inside the README — so the
person was right, the article's URL was just stale, the same class of error
as `ryoppippi/ccusage` → `ccusage/ccusage`. Lesson: a 404 on a cited owner
means "check for a rename/org transfer," not "fabricated." Corrected:

- **Repo:** [`headroomlabs-ai/headroom`](https://github.com/headroomlabs-ai/headroom) —
  59,878 stars, created 2026-01-07 (~6.5 months old), Apache 2.0, Python.
- **Author:** Tejas Chopra, described in independent coverage as a Netflix
  senior engineer who built it to solve his own token-cost problem.
- **What it does:** compresses tool outputs, logs, RAG chunks, and files
  before they reach the LLM. Three modes: library (`compress()`), HTTP
  proxy (`headroom proxy`), or MCP server. Has a CCR (Compress-Cache-Retrieve)
  feature — compressed originals are cached locally and retrievable via a
  `headroom_retrieve` tool, which is a real mitigation for the "lossy
  compression loses exact strings" problem other tools in this space have.
- **Independent evidence it's real:** #2 on Trendshift's monthly trending
  (June 2026, a third-party tracker, not self-reported) — [Trendshift](https://trendshift.io/repositories/21654);
  coverage on [DEV Community](https://dev.to/arshtechpro/headroom-cut-your-llm-token-usage-by-up-to-95-without-changing-your-answers-5g06),
  [Medium](https://subratpati.medium.com/building-cost-efficient-agents-with-headroom-context-compression-for-llm-applications-b665128153b6),
  and a real GitHub issue thread with substantive technical back-and-forth
  ([`headroomlabs-ai/headroom#1158`](https://github.com/chopratejas/headroom/issues/1158)).
- **Remaining risk:** it's still a proxy, and setup downloads a compression
  model from HuggingFace plus an ONNX runtime from `cdn.pyke.io` — real
  external dependencies at install time, not zero-risk, just not
  fabricated.

### Caveman: real, but the marketed number is not the real-world number

- **Repo:** [`JuliusBrussee/caveman`](https://github.com/JuliusBrussee/caveman) —
  90,619 stars, 252 commits, 181 open issues, 229 PRs, v1.9.1 (July 2026).
  Structure (`install.sh`/`.ps1`, `SECURITY.md`, `benchmarks/`, `tests/`,
  6 compression levels, multi-agent support) reflects real, actively
  maintained software, not a shell repo.
- **Mechanism:** purely a system-prompt/skill instruction — no code
  execution, no proxying, no network interception. Lowest technical risk
  of any tool discussed in this note.
- **Marketed claim:** ~65% average output-token reduction.
- **Independent reality** (multiple outlets converge on similar numbers —
  [andrew.ooo](https://andrew.ooo/posts/caveman-claude-code-skill-token-savings-review/),
  [Towards AI](https://pub.towardsai.net/i-tested-the-viral-caveman-ai-trick-heres-what-it-actually-saves-and-what-it-doesn-t-83aa5f0a093c),
  [JetBrains AI blog](https://blog.jetbrains.com/ai/2026/07/speak-to-ai-agents-like-cavemen-tosave-tokens/)):
  the 65% figure is real but applies **only to output tokens** — the
  cheapest part of a Claude Code bill. It doesn't touch input, cache, or
  reasoning tokens, and the skill itself adds ~1–1.5k input tokens per turn.
  Real-world impact on **total session cost**: roughly **4–10% for mixed
  workflows, near zero for pure code-generation sessions** — and it can go
  net-negative on already-terse workloads.

### Karpathy Skills: content is fine, headline stars are inflated by double-counting

- **Repo:** [`multica-ai/andrej-karpathy-skills`](https://github.com/multica-ai/andrej-karpathy-skills),
  created 2026-01-27 by an identified individual (Forrest Chang, per
  [Tech Times](https://www.techtimes.com/articles/316798/20260518/karpathy-inspired-claudemd-passes-220000-combined-github-stars-four-rules-that-stop-ai-breaking.htm)).
  Reached #1 on GitHub Trending 2026-04-08.
- **The "220k stars" headline is a counting trick, not one repo's count:**
  ~91.2k stars on a personal-account copy + ~132k on the org mirror,
  reported combined. Each individual repo's star count is real (verified
  via API on the org copy: 194,097), but summing two repos with the same
  content to get a bigger single number is a known vanity-metric tactic —
  worth knowing even though it isn't outright fake stars.
- **Content is genuinely fine:** fetched and read the actual `CLAUDE.md` —
  four principles (Think Before Coding, Simplicity First, Surgical Changes,
  Goal-Driven Execution). Generic, sensible, no red flags, no prompt-injection
  payloads. It's a markdown file, not code — essentially zero execution risk.
- **Caveat worth keeping:** security researchers have documented CLAUDE.md
  files as a real prompt-injection vector in general (unrelated to this
  specific repo) — always read a CLAUDE.md before adopting it, never trust
  one blindly just because it's popular.

### The bigger context: GitHub star inflation is real and well-documented

A peer-reviewed ICSE 2026 study (CMU/NCSU/Socket) found **~6 million
suspected fake stars across 18,617 repos from ~301,000 accounts**, with
AI/LLM tooling as the largest non-malicious target category (177,000 fake
stars) — stars are bought for $0.03–$0.90 each on markets including Fiverr,
specifically because VCs and developers use star count as a trust signal
([arXiv](https://arxiv.org/html/2412.13459v2), [ICSE 2026 paper](https://cmustrudel.github.io/papers/icse2026fakestars.pdf)).
This confirms the original skepticism was a reasonable prior — AI dev tools
are exactly the category most targeted — but it doesn't mean every fast-growing
repo in this category is fake. `headroom`, `caveman`, and the Karpathy skills
repo all have independent, non-repo-owned evidence of real adoption
(third-party trending trackers, multiple independent reviewer write-ups,
real GitHub issue discussions). The honest read: **real tools, inflated
marketing, star counts not fully trustworthy as a popularity signal either
way.**

### The number that actually matters: real-world combined impact

The most important independent data point found: **replayed against a
614M-token corpus of real Claude Code sessions, `rtk` + `headroom` +
`caveman` together saved only 3.7% of total spend** — far below any of the
individual marketed numbers — because the bill is dominated by
`cache_create` (42%) and output (29%), streams these tools barely touch
([Substack](https://codepointer.substack.com/p/cutting-llm-token-costs-with-rtk)).
Headroom alone saved ~2.8% of spend in that same replay, despite hitting
70–90% reduction on the specific structured content (JSON, logs, RAG
chunks) it targets — because that content isn't most of the bill.

### Verdict: worth it?

**Yes, cautiously, with recalibrated expectations.** These are legitimate,
actively-maintained tools with real (if modest) benefit — not the scam
cluster the first pass's data (misled by one bad URL) suggested. But none
of them are a 60–90% fix; expect low-single-digit-to-low-teens percent
savings on total spend, not the marketed headline numbers.

### Recommended setup

1. **Adopt now, no real downside:**
   - **Karpathy-style CLAUDE.md principles** — write our own short version
     of the four principles (Think Before Coding, Simplicity First,
     Surgical Changes, Goal-Driven Execution) into
     [`../agents/claude-code/README.md`](../agents/claude-code/README.md)
     and as a starting-point CLAUDE.md for new projects. Zero execution
     risk (it's just text), and the mechanism (fewer wrong-assumption
     rewrites) isn't captured by the token-savings numbers above at all —
     it plausibly matters more than any compression tool. Written in our
     own words rather than adopting the repo, since it's generic advice
     unaffiliated with Karpathy despite the name.
   - **`ccusage`** (already concluded above, corrected URL
     `github.com/ccusage/ccusage`) — measure first, always.

2. **Adopt situationally:**
   - **`caveman`** — install it, but treat it as a per-session toggle
     (`/caveman`) for explanation-heavy or verbose-reply-prone sessions,
     not an always-on default. Expect ~5–10% total-session savings on
     mixed work, not 65%. Skip it for pure code-generation work where it
     can go net-negative.

3. **Evaluate hands-on before committing:**
   - **`headroom`** — legitimate but heavier (external model/runtime
     download, runs a local proxy). Worth it specifically for RAG-heavy
     or huge-log/JSON-heavy workflows where its 70–90% reduction on that
     content type is real; not worth the proxy-mode complexity for typical
     coding sessions where it nets ~2.8% of spend. If tried, start with
     library or MCP-server mode rather than wrapping all Claude Code
     traffic through the proxy by default.

4. **Still not recommended:** `rtk`, `pxpipe`, `graphify` — not part of
   this follow-up's scope, original caution from the first pass stands
   (unaudited proxies / unverified authorship) unless separately
   re-investigated the way `headroom` was here.

**Bottom line for actual spend management:** the process habits the
original article listed for free — aggressive `/clear` between tasks, lean
CLAUDE.md files, routing simple work to cheaper models, one accurate pass
instead of five cheap ones — likely matter more than any single tool here,
since the real-world replay data shows the tools only nibble at a bill
dominated by cache and output tokens that habits directly address.
