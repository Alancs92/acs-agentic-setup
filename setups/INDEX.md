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
| [`claude-skills-backup/`](claude-skills-backup/README.md) | active | Claude Code | local + OneDrive | Versioned, hash-deduped backup of hand-authored config (skills, commands, hooks, settings, plugin manifests) via `claude-acs backup`. Snapshots only what changed, prunes on an age-tiered retention policy, blobs to OneDrive, committed `catalog.jsonl` audit trail. |
