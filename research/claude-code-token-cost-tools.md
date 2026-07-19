# Claude Code token-cost reduction stack (2026 "viral Reddit" tools)

- **Date:** 2026-07-19
- **Status:** open — verification done, no setup started yet pending a decision

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
| Headroom | **No** | `chopratejas/headroom` does not exist. Only unrelated companion repos exist under that owner (`headroom-zed`, 50 stars; `headroom-swift`, 8 stars) — neither is the described flagship tool. Yet dozens of *other* unrelated repos reference "Headroom" / `chopratejas/headroom` as an established, real tool (dashboards, launchers, plugins built "for" it). | **Cannot verify — likely fabricated or removed.** The most concrete finding in this whole investigation: the article's own recommended flagship tool doesn't exist under the cited owner, while a surrounding swarm of small repos treats it as real. |

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
