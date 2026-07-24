# setups/claude-code-account-profiles/scripts/

| File | What it does |
|---|---|
| [`apply_profile.py`](apply_profile.py) | Resolves the manifest for one account and applies it to that account's `settings.json` (plugins + marketplaces) and, with `--prune-skills`, its skills. Stdlib only. Safe by default: backup, JSON-validate, idempotent, reversible skill moves. |
| [`test_apply_profile.py`](test_apply_profile.py) | 17 stdlib `unittest` tests — resolver logic, the golden `59a9cd93…` hash anchor, key preservation, backup, idempotency, dry-run, and skill-prune (opt-in / symlink / shared-dir) behavior. Run: `python3 test_apply_profile.py`. |

Usage: see [`../README.md`](../README.md). Entry point is
`claude-acs apply-profile <account>`.
