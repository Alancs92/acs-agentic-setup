# setups/claude-code-account-profiles/scripts/

| File | What it does |
|---|---|
| [`apply_profile.py`](apply_profile.py) | Resolves the manifest for one account and applies it to that account's `settings.json` (plugins + marketplaces) and, with `--prune-skills`, its skills. Stdlib only. Safe by default: backup, JSON-validate, idempotent, reversible skill moves. |
| [`mirror_account.py`](mirror_account.py) | Keeps a **mirror account** (profiles.json `mirrors`, e.g. `terradev` → `personal`) in parity: links `CLAUDE.md`/`hooks`/`agents`/`memory` + per-project auto-memory to the source, copies `settings*.json`/`.mcp.json` with account paths rewritten, merges only `mcpServers` + trust flags into `.claude.json`. `--check` exits 1 on drift. Entry: `claude-acs mirror`. |
| [`test_mirror_account.py`](test_mirror_account.py) | 14 temp-dir tests: links, memory adoption/conflicts, path rewrite, identity never copied, idempotency, `--check`/`--dry-run`, apply-profile refusing a mirror. |
| [`test_apply_profile.py`](test_apply_profile.py) | 17 stdlib `unittest` tests — resolver logic, the golden `59a9cd93…` hash anchor, key preservation, backup, idempotency, dry-run, and skill-prune (opt-in / symlink / shared-dir) behavior. Run: `python3 test_apply_profile.py`. |

Usage: see [`../README.md`](../README.md). Entry point is
`claude-acs apply-profile <account>`.
