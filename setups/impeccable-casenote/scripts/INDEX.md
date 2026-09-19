# setups/impeccable-casenote/scripts/

| File | What it covers |
|---|---|
| [`fetch_engine.py`](fetch_engine.py) | Resolves the pinned Impeccable engine from its platform npm package, verifies integrity, records provenance. `--print-export` emits the `IMPECCABLE_BIN` line; `--print-sha256` prints the resolved binary's hash for filling the lock. |
| [`engine.lock.json`](engine.lock.json) | Committed provenance: CLI + engine versions, platform, binary sha256, route, date. The binary itself is gitignored. |
| [`casenote_lint.py`](casenote_lint.py) | Casenote denylist checker — 9 rules. Exit 0/1/2, `--json`, `--quiet`. |
| [`test_fetch_engine.py`](test_fetch_engine.py) | 11 tests: hashing, SRI verification, lock round-trip, tamper detection, fail-closed `main`. |
| [`test_casenote_lint.py`](test_casenote_lint.py) | 24 tests: the clean anchor, one violation per rule, legal-direction counter-tests, exit codes, JSON shape. |
| [`fixtures/`](fixtures/INDEX.md) | Test fixtures. |

Run both suites: `uv run --with pytest python -m pytest -q` from this directory.
