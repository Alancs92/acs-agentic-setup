# setups/

Concrete, reproducible agentic setups — each one self-contained in its own
directory with a `README.md` (what/why/how to reproduce) plus whatever
`scripts/`/`config/` it needs. This is the "history" layer: every setup I've
actually stood up, including retired ones (marked `archived` rather than
deleted).

Add a new one with [`../templates/setup-template.md`](../templates/setup-template.md).

| Setup | Status | Agents | Environment | Summary |
|---|---|---|---|---|
| [`claude-code-web-github/`](claude-code-web-github/README.md) | active | Claude Code | cloud (remote execution environment) | Claude Code on the web, single agent, wired to a GitHub repo via MCP with a branch-per-task + draft-PR + auto-watch workflow. |
| [`claude-code-account-profiles/`](claude-code-account-profiles/README.md) | active | Claude Code | local | Shared lean-core plugin/skill profile applied per account (personal/harrison/personal2) via `claude-acs apply-profile`, with per-account tweaks. Implements the token-usage audit's lean profile. |
| [`claude-code-usage-stats/`](claude-code-usage-stats/README.md) | active | Claude Code | local | Cross-account usage stats via `claude-acs stats`: versioned `stats.json` + standalone 13-panel HTML dashboard over prompt history and session transcripts, with `(timestamp, display)` dedup across accounts. |
| [`impeccable-brand-lint/`](impeccable-brand-lint/README.md) | active | Claude Code | local | Brand-agnostic design linting: version-pinned Impeccable detector (provenance-locked) plus a 9-rule checker whose brand-specific half is driven by swappable `brands/*.json` profiles. Add a brand without touching code; `brand-guidelines` stays the single source of truth, hash-linked so drift fails loudly. |
