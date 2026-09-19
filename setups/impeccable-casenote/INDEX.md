# setups/impeccable-casenote/

Mechanical design linting for personal projects: upstream
[Impeccable](https://github.com/pbakaus/impeccable)'s detector, version-pinned
and provenance-locked, plus a checker for Casenote's own denylist — composed so
neither contradicts `brand-guidelines`, which stays the single source of truth
for token values.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | What/why/reproduce, the authority model, the combined CI step, and the re-runnable evidence probes. Start here. |
| [`design.md`](design.md) | The design: drivers, evidence, authority model, component architecture, exit-code contract, testing. |
| [`implementation-plan.md`](implementation-plan.md) | The six TDD tasks this was built from. |
| [`config/`](config/INDEX.md) | The `.impeccable/config.json` template. |
| [`scripts/`](scripts/INDEX.md) | `fetch_engine.py`, `casenote_lint.py`, their suites and the provenance lock. |

Origin: [`../../research/impeccable-design-skill.md`](../../research/impeccable-design-skill.md).
