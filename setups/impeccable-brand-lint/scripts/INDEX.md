# setups/impeccable-brand-lint/scripts/

| File | What it covers |
|---|---|
| [`fetch_engine.py`](fetch_engine.py) | Resolves the pinned Impeccable engine from its platform npm package, verifies integrity, records provenance. `--print-export` emits the `IMPECCABLE_BIN` line. Brand-agnostic. |
| [`engine.lock.json`](engine.lock.json) | Committed provenance: CLI + engine versions, platform, binary sha256, route, date. The binary itself is gitignored. |
| [`brand_lint.py`](brand_lint.py) | The linter. 5 universal rules + 4 driven by a `--brand` profile. `--list-rules`, `--emit-impeccable-config`, `--no-check-source`. Exit 0/1/2. |
| [`test_fetch_engine.py`](test_fetch_engine.py) | 11 tests: hashing, SRI verification, lock round-trip, tamper detection, fail-closed `main`. |
| [`test_brand_lint.py`](test_brand_lint.py) | 40 tests, including the synthetic `Acme` brand that proves the engine is brand-agnostic. |
| [`fixtures/`](fixtures/INDEX.md) | Test fixtures. |

Run both suites: `uv run --with pytest python -m pytest -q` from this directory.
