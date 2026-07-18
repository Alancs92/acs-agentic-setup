# acs-agentic-setup

A living record of my agentic setups: which AI agents (Claude, Codex, Hermes,
Ollama, etc.) I run, how each one is configured, how they're combined, and
the scripts that stand them back up. It's history (what I ran and when),
reference (how a given config actually works), and a workbench (where new
combinations get assembled) all in one place.

This is not a single opinionated framework — it's intentionally wide and
multi-dimensional. A "setup" can be one agent running locally, one agent
running in the cloud, several agents cooperating, or something narrower like
a single hook script. The structure below exists to keep that sprawl
navigable, for me and for any agent reading this repo as context.

## Navigation contract

Every directory has an `INDEX.md`. Read `README.md` (this file) first, then
only the `INDEX.md` files relevant to what you need — you should never have
to read a whole subtree to find something. Each `INDEX.md` lists what's in
that directory, one line per entry, and links deeper only where needed.

## Map of content

| Directory | What lives there |
|---|---|
| [`agents/`](agents/INDEX.md) | Docs on individual agent tools themselves — Claude Code, Codex, Hermes, Ollama, etc. Install, config surface, capabilities, quirks. Not tied to any one combination. |
| [`setups/`](setups/INDEX.md) | Concrete, reproducible setups — one agent or several, local or cloud — each with what it is, why it exists, and the scripts/config to reproduce it. This is the "history" of what I've actually run. |
| [`research/`](research/INDEX.md) | Open notes: comparisons, evaluations, experiments, links — things I'm exploring that haven't crystallized into a setup yet. |
| [`scripts/`](scripts/INDEX.md) | Shared automation not tied to a single setup (bootstrap helpers, common install steps, etc.). Setup-specific scripts live inside that setup's own directory instead. |
| [`templates/`](templates/INDEX.md) | Copy-paste starting points for adding a new agent doc, setup, or research note, so new entries stay structurally consistent. |
| [`CHANGELOG.md`](CHANGELOG.md) | Dated, human-readable log of notable changes across setups — the append-only timeline view of this repo's history. |

## Conventions

- **One `INDEX.md` per directory.** When you add a subdirectory, add an
  `INDEX.md` to it in the same commit, and add a line for it to the parent's
  `INDEX.md`.
- **Setups are self-contained.** Everything needed to reproduce a setup
  (README, scripts, config) lives inside `setups/<slug>/`. Don't scatter a
  setup's files across the repo.
- **Use the templates** in `templates/` when adding a new agent, setup, or
  research note — they carry the metadata (date, status, agents involved)
  that keeps entries scannable without opening every file.
- **Don't delete history.** If a setup is retired, mark it `archived` in its
  own README rather than deleting the directory — the point of this repo is
  to remember what was tried, including what didn't stick.
- **No secrets.** Config and scripts in this repo are for structure and
  reference — credentials, tokens, and API keys never get committed here.
