# setups/claude-code-account-profiles/

Shared lean-core Claude Code plugin/skill profile applied per account, with
per-account tweaks. Source of truth is `profiles.json`; applied via
`claude-acs apply-profile <account>`.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | The setup: what it is, why, and how to reproduce/apply. |
| [`design.md`](design.md) | Approved design + implementation notes (shared-skills finding, `--prune-skills` opt-in). |
| [`profiles.json`](profiles.json) | The manifest: `core` + per-account overlays. |
| [`scripts/`](scripts/INDEX.md) | `apply_profile.py` (resolver + applier) and its tests. |

Related: [`../../research/token-usage-audit.md`](../../research/token-usage-audit.md)
(the audit this setup implements) and its
[`baseline-metric-methodology.md`](../../research/baseline-metric-methodology.md).
