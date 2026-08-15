# Changelog

Dated, human-readable log of notable changes to setups documented in this
repo. This is the timeline view — for the structural map, see
[`README.md`](README.md). Newest entries first.

## 2026-08-15

- First research note: [`research/old-coder-evidence-first.md`](research/old-coder-evidence-first.md)
  — evaluation of the `old-coder` skill's SPEC → GAUNTLET → EVIDENCE loop
  against what `sediment` and `life-and-times` already enforce, with a
  reproducible benchmark in [`research/old-coder-benchmark/`](research/old-coder-benchmark/INDEX.md)
  measuring defect detection per process regime. Recommendation: adopt the
  spec artifact, adopt mutation testing only as a periodic audit, drop
  coverage as a quality signal. Turned up two live issues in `sediment`
  (fail-open coverage gate in CI; timezone-naive timestamp parsing).

## 2026-07-18

- Initial scaffolding: repo structure (`agents/`, `setups/`, `research/`,
  `scripts/`, `templates/`), navigation conventions, and templates.
- First documented setup: [`setups/claude-code-web-github`](setups/claude-code-web-github/README.md)
  — Claude Code on the web running in a remote execution environment with
  the GitHub MCP integration, branch-per-task workflow, and PR auto-watch.
- `main` initialized as a minimal placeholder branch so this scaffolding
  could land through a reviewable pull request.
