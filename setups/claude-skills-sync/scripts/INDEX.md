# setups/claude-skills-sync/scripts/

Stdlib-only Python 3. No `__init__.py`, so run tests from inside this directory:
`python3 -m unittest discover -p 'test_*.py'` (75 tests).

| File | What it does |
|---|---|
| [`skills_sync.py`](skills_sync.py) | The CLI and orchestrator: `pull`, `status`, `install-schedule`. Holds the six pull gates and the exit-code contract (0 fine / 1 refused / 2 attention). Entry point for `claude-acs skills`. |
| [`gitops.py`](gitops.py) | The **only** module permitted to invoke git. Holds `GIT_SUBCOMMANDS`, an allow-list of subcommands and per-subcommand flags that fails closed, so `reset`/`clean`/`stash`/`checkout`/`restore`/`rebase`/`pull`/`--force` are unreachable rather than merely unused. Also remote-URL normalisation and `RepoState`. |
| [`runlog.py`](runlog.py) | The append-only run log: the closed set of `OUTCOME_*` values, the line format, and parsing that tolerates launchd tracebacks interleaved with structured lines. |
| [`schedule.py`](schedule.py) | launchd plist generation from `config.json`'s schedule block, plus label validation (a label becomes a filename) and log-path resolution under `~/Library/Logs`. |
| [`test_skills_sync.py`](test_skills_sync.py) | 75 stdlib `unittest` tests over throwaway git repos in `tempfile.TemporaryDirectory()`. Asserts the safety negatives (dirty file byte-identical, HEAD unmoved, local commits intact, no merge commit) and statically audits this package's own AST for destructive verbs, `shell=True`, and any route to git that bypasses `gitops.run_git`. |

Import direction is one-way: `skills_sync` → `gitops` / `runlog` / `schedule`.
The three helpers do not import each other, and none of them imports
`skills_sync`.

Usage: see [`../README.md`](../README.md). Entry point is `claude-acs skills`.
