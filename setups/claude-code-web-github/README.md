# Claude Code on the web + GitHub

- **Created:** 2026-07-18
- **Status:** active
- **Agents involved:** Claude Code
- **Environment:** cloud (managed remote execution environment)
- **Topology:** single agent

## What this is

Claude Code running via claude.ai/code (or another surface that launches
Claude Code on the web) instead of the local CLI. Each session gets an
isolated, ephemeral container: the target repo is cloned fresh when the
container starts, and the container is reclaimed after a period of
inactivity — so nothing survives unless it's committed and pushed before the
session ends. This repo (`acs-agentic-setup`) was scaffolded from inside
exactly this kind of session.

## Why

Lets agentic coding work happen without a local machine running or a repo
checked out anywhere persistent — useful for kicking off work from a phone
or browser, for background/scheduled work, and for keeping the blast radius
of a session contained to a throwaway container.

## Components

- **Claude Code** — the agent itself, running in the remote container.
- **GitHub MCP server** — all GitHub interaction (reading/creating PRs and
  issues, comments, CI status, branches) goes through MCP tools rather than
  a `gh`/`hub` CLI, which isn't available in this environment.
- **Repository scope** — the session is explicitly scoped to one or more
  repos; anything outside that scope is denied even if a broader search tool
  could technically reach it. Additional repos can be added mid-session on
  explicit request.

## Reproducing it

There's no bootstrap script — the environment is provisioned by the
platform when a session starts (environment kind, network policy, env vars,
and setup scripts are chosen at environment-creation time). To reproduce
this setup:

1. Start a Claude Code on the web session against the target repo(s).
2. Confirm the GitHub MCP server is available (`get_me` should resolve).
3. Adopt the workflow conventions below via the session's instructions.

## Workflow conventions

- **Branch per task.** Work happens on a dedicated branch
  (`claude/<slug>`), never directly on the default branch.
- **Draft PRs by default.** After pushing, open a draft PR automatically if
  one doesn't already exist for the branch (skip if one is merged/closed —
  that's finished work, not something to reuse).
- **Auto-watch after opening.** Subscribe to PR activity right after
  creating a PR so review comments and CI failures arrive as events into the
  session instead of requiring manual polling.
- **Push retry policy.** Network-failure pushes retry with exponential
  backoff (2s/4s/8s/16s) rather than failing immediately.
- **No forced or history-rewriting operations** (force-push, hard reset)
  without explicit confirmation — the container is disposable, but the
  remote branch and PR are not.

## Notes / known issues

- `gh`/`hub` CLI and direct GitHub API access are not available in this
  environment — every GitHub operation must go through the MCP tools.
- Webhook-delivered PR events (`<github-webhook-activity>`) don't cover
  everything (e.g. plain CI success with no comment) — a subscribed session
  should still periodically re-check PR/CI state rather than relying on
  events alone.
- Because the container is ephemeral, any local-only script, cache, or
  credential set up mid-session disappears when the session ends — durable
  setup belongs in the environment's configuration, not in the container.
