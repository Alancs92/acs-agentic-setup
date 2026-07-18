# agents/

Docs on individual agent tools themselves — what they are, how to install
and configure them, what their config surface looks like, and quirks worth
remembering. This layer is agent-agnostic of any particular combination or
environment; combinations live in [`../setups/`](../setups/INDEX.md).

Add a new agent with [`../templates/agent-template.md`](../templates/agent-template.md).

| Agent | Status | Summary |
|---|---|---|
| [`claude-code/`](claude-code/INDEX.md) | documented | Anthropic's Claude Code CLI/web agent — the tool this repo itself is being written with. |
| [`codex/`](codex/INDEX.md) | stub | OpenAI Codex CLI agent — not yet documented. |
| [`hermes/`](hermes/INDEX.md) | stub | Hermes (open-weight model family, e.g. via Nous Research) — not yet documented. |
| [`ollama/`](ollama/INDEX.md) | stub | Local model runner used to host open-weight models for agentic use — not yet documented. |
