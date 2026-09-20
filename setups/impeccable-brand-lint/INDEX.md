# setups/impeccable-brand-lint/

Brand-agnostic mechanical design linting: upstream
[Impeccable](https://github.com/pbakaus/impeccable)'s detector, version-pinned
and provenance-locked, plus a 9-rule checker whose brand-specific half is driven
by swappable profiles. The engine holds no brand's values; `brand-guidelines`
stays the single source of truth for what a brand actually is.

| File | What it covers |
|---|---|
| [`README.md`](README.md) | What/why/reproduce, the universal-vs-brand rule split, profile sync, the combined CI step. Start here. |
| [`design.md`](design.md) | The design, including the 2026-09-20 revision that made it themeless and what v1 got wrong. |
| [`implementation-plan.md`](implementation-plan.md) | How v1 was built. Partly superseded — kept as the record. |
| [`brands/`](brands/INDEX.md) | One JSON profile per brand. Add a brand here; `scripts/` does not change. |
| [`scripts/`](scripts/INDEX.md) | `fetch_engine.py`, `brand_lint.py`, their suites and the provenance lock. |

Origin: [`../../research/impeccable-design-skill.md`](../../research/impeccable-design-skill.md).
