# setups/impeccable-casenote/

Mechanical design linting for personal projects: upstream
[Impeccable](https://github.com/pbakaus/impeccable)'s detector, vendored and
version-pinned, plus a checker for Casenote's own denylist — composed so neither
contradicts `brand-guidelines`, which stays the single source of truth for token
values.

**Status: designed, not yet implemented.** Only `design.md` exists so far.

| File | What it covers |
|---|---|
| [`design.md`](design.md) | The design: drivers, the evidence probes behind it, authority model, component architecture, exit-code contract, testing. |

Planned additions on implementation: `README.md` (what/why/reproduce),
`config/impeccable.json` (the `ignoreRules` template), and `scripts/` with
`fetch_engine.py`, `casenote_lint.py`, their tests and `engine.lock.json` —
each with its own `INDEX.md` per the navigation contract.

Origin: [`../../research/impeccable-design-skill.md`](../../research/impeccable-design-skill.md).
