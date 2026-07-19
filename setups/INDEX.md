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
