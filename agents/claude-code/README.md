# Claude Code

Anthropic's agentic coding tool. Ships as a terminal CLI, a desktop app
(Mac/Windows), a web app (claude.ai/code — "Claude Code on the web"), and IDE
extensions (VS Code, JetBrains). Same underlying agent; the surface changes
where and how it runs (local shell vs. a managed remote container).

## Config surface

| Piece | Where | Purpose |
|---|---|---|
| `settings.json` | `~/.claude/settings.json` (user, global) and `.claude/settings.json` / `.claude/settings.local.json` (project) | Permissions (allow/ask/deny per tool or command pattern), env vars, hooks, model defaults. `settings.local.json` is for machine-local overrides and is typically gitignored. |
| `CLAUDE.md` | Repo root (and nested, closest wins) | Project-specific instructions loaded into every session's context automatically — conventions, build/test commands, gotchas. |
| Skills | `.claude/skills/<name>/SKILL.md` (project) or user-level | Packaged instructions for a recurring workflow, invoked via `/name` or auto-triggered by description matching. |
| Hooks | Configured in `settings.json` | Shell commands that fire on events (session start, before/after tool use, on stop) — e.g. auto-run linters, block risky commands. |
| MCP servers | `.mcp.json` or `claude mcp add` | External tool/data integrations (GitHub, Slack, databases, etc.) exposed to the agent as additional tools. |
| Subagents | `.claude/agents/*.md` | Named agent personas with their own system prompt, tool allowlist, and optionally a specific model — invoked via the `Agent` tool for parallel or isolated work. |
| Slash commands | `.claude/commands/*.md` | Reusable prompt snippets invoked as `/command-name`. |

## Where it runs

- **Local (CLI)**: full shell access, reads the local filesystem, permission
  prompts happen interactively in the terminal.
- **Remote execution environment (Claude Code on the web)**: runs in an
  isolated, ephemeral container per session; the repo is cloned fresh at
  container start and reclaimed after inactivity, so anything worth keeping
  must be committed and pushed. Network access is governed by the
  environment's configured policy. See
  [`../../setups/claude-code-web-github/`](../../setups/claude-code-web-github/README.md)
  for a documented instance of this.

## Notes / gotchas

- Config resolution is layered (user → project → local), and the most
  specific one wins on conflicts — check all three before assuming a setting
  isn't applied.
- Remote/web sessions don't have `gh`/`hub` CLI access by default; GitHub
  operations go through an MCP server instead.
- Hooks run as real shell commands with the harness's permissions — treat
  them like any other executable you'd review before trusting.
