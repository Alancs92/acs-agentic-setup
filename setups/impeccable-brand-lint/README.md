# Impeccable + brand lint

- **Created:** 2026-09-20
- **Status:** active
- **Agents involved:** Claude Code (any harness — these are plain CLIs)
- **Environment:** local
- **Topology:** single agent

## What this is

Mechanical design enforcement that is **not bound to any one brand**. Two
composable checks:

1. **Upstream [Impeccable](https://github.com/pbakaus/impeccable)'s detector** —
   deterministic anti-pattern rules in a Rust binary, version-pinned with
   provenance recorded in `scripts/engine.lock.json`.
2. **`brand_lint.py`** — 9 rules. Five are **universal** (structural mistakes any
   design system would call a mistake). Four are **parameterised by a brand
   profile**.

Both share one exit-code contract (`0` clean, `1` unscannable or stale profile,
`2` findings), so they compose in a single CI step.

**The engine holds no brand's values.** Swap the profile, and the same binary
enforces a different brand:

```
$ brand_lint.py --json probe.html                     # 0 findings — universal rules only
$ brand_lint.py --brand casenote --json probe.html    # forbidden-token-names, gradient-only-as-mark, series-ceiling
$ brand_lint.py --brand acme.json --json probe.html   # gradient-only-as-mark, series-ceiling  (Acme's tokens, not Casenote's)
```

## Why it's built this way

Impeccable itself is brand-agnostic; an early version of this setup wrapped it in
a way that was not, hard-coding one brand's hexes and token names into the rule
functions. That was the wrong shape: the `brand-guidelines` skill already
supports multiple brands (Casenote, Harrison.ai, Anthropic) as independent
subsets, and the linter should mirror it rather than fight it.

| Rule | Kind | Values from |
|---|---|---|
| `raw-hex` | universal | — |
| `pure-black-on-white` | universal | — |
| `light-dark-with-theme-stamp` | universal | — |
| `theme-on-root` | universal | — |
| `svg-no-min-width` | universal | — |
| `forbidden-token-names` | brand | `profile.forbidden_tokens` |
| `gradient-only-as-mark` | brand | `profile.gradient_only` |
| `status-as-series` | brand | `profile.status_tokens` |
| `series-ceiling` | brand | `profile.series.ceiling` |

`brand_lint.py --list-rules` prints this table from the code, so it cannot drift.

A brand rule with nothing configured returns **no findings** rather than falling
back to a default brand. An unconfigured run is honest, not accidentally
Casenote-shaped.

## Keeping a profile in sync with its brand

A profile is hand-authored and records the `source_sha256` of the brand markdown
it was read from. Before linting, the tool checks it:

```
$ brand_lint.py --brand casenote src/
error: …/brands/alan-personal.md changed since this profile was reviewed
       (recorded d9cad27fe7e0…, found 4b1e90ac22f1…). Re-check the profile
       against the brand file, then update source_sha256.
```

That is the sync mechanism — explicit, not magic. Deriving profiles by parsing
the brand markdown was considered and rejected: the three brand files have
different section shapes, so a parser tuned to one silently returns an empty
profile for the others, and empty reads as "clean".

`--no-check-source` skips the gate. A profile with no `source_sha256` (a brand
whose markdown isn't on this machine) is never checked.

## Authority model

| Concern | Owner |
|---|---|
| A brand's actual definition — colour, type, layout | `brand-guidelines` skill, `brands/*.md` |
| Its machine-checkable parameters | `brands/<brand>.json` here (hash-linked to the above) |
| Chart form — palettes, stat tiles, legends | `dataviz` |
| Generic slop rules | vendored Impeccable, minus what the profile suppresses |

The brand file stays the single source of truth. A profile restates only what a
checker needs, and the hash makes any drift loud.

> **Known gap.** `brands/alan-personal.md` cites
> `dataviz/scripts/validate_palette.js` as the validator that caught two
> invisible palette defects. That script is not resolvable on disk — `dataviz`
> appears to be a managed skill whose files are not local.

## Reproducing it

```bash
# 1. Resolve and verify the pinned engine (once per machine)
python3 scripts/fetch_engine.py
eval "$(python3 scripts/fetch_engine.py --print-export)"

# 2. Generate the detector config from the SAME profile, so one brand drives both
python3 scripts/brand_lint.py --brand casenote --emit-impeccable-config \
  > <project>/.impeccable/config.json

# 3. Run both checks
"$IMPECCABLE_BIN" detect <project>/src
python3 scripts/brand_lint.py --brand casenote <project>/src
```

### Combined CI step

No `set -e`: both tools exit non-zero by design, and `set -e` would abort on the
very codes this needs to read.

```bash
#!/usr/bin/env bash
BRAND=casenote
eval "$(python3 scripts/fetch_engine.py --print-export)" || exit 1

"$IMPECCABLE_BIN" detect --json src/ ; imp=$?
python3 scripts/brand_lint.py --brand "$BRAND" --json src/ ; own=$?

if [ "$imp" -eq 1 ] || [ "$own" -eq 1 ]; then exit 1; fi   # unscannable / stale profile
if [ "$imp" -eq 2 ] || [ "$own" -eq 2 ]; then exit 2; fi   # findings
exit 0
```

## Adding a brand

Nothing in `scripts/` changes. See [`brands/INDEX.md`](brands/INDEX.md) —
copy a profile, set the parameters, record the source hash, add a row.

## Why both tools, concretely

The Casenote clean fixture was written by hand and looked right. Upstream's
detector found two real defects in it that `brand_lint.py` did not:

- **`low-contrast`, 1.7:1.** The dark-mode blocks re-declared `--paper`, `--ink`,
  `--body` and `--accent` but not `--surface`, so `.card` kept the *light*
  surface under light-on-dark text.
- **`cramped-padding`.** A bordered, filled container with no inset.

The first is exactly the kind of rule `brand_lint.py` **cannot** encode — it
needs cascade resolution, not text matching. Upstream caught it by a different
strategy: computing the contrast ratio rather than parsing the cascade.

They fail differently, and the gap in one is covered by the other.

## Testing

```bash
cd scripts && uv run --with pytest python -m pytest -q    # 51 tests
```

The suite includes a **second, synthetic brand** (`Acme`) whose token names
overlap Casenote's nowhere. It asserts that Acme's violations are caught, and
that Casenote's tokens are *invisible* under the Acme profile. That test is what
actually proves the engine is brand-agnostic — everything else could pass with a
Casenote-shaped engine.

## Notes / known issues

- **Not for Harrison.ai repos** pending the supply-chain review in the
  evaluation. Personal projects only. (The *capability* to add a `harrison-ai`
  profile exists; the decision to use it here does not.)
- **Not installed as a plugin, and no post-edit hook.** Deliberate cuts; see
  design.md's non-goals.
- **`brand_lint.py` checks text, not the cascade.** An SVG sized from a
  stylesheet rather than its own tag is not caught, and "a second accent" is not
  encoded at all — it needs judgment, and false positives train you to ignore
  the tool.
