# Impeccable + Casenote design linting

- **Created:** 2026-09-20
- **Status:** active
- **Agents involved:** Claude Code (any harness — the tools are plain CLIs)
- **Environment:** local
- **Topology:** single agent

## What this is

Two runnable checks for personal projects with UI:

1. **Upstream [Impeccable](https://github.com/pbakaus/impeccable)'s detector** —
   61 deterministic anti-pattern rules in a Rust binary, version-pinned and
   resolved from its platform npm package with provenance recorded in
   `scripts/engine.lock.json`.
2. **`casenote_lint.py`** — 9 rules encoding the mechanically-testable half of
   Casenote's "NOT this brand" denylist.

Both share one exit-code contract (`0` clean, `1` unscannable, `2` findings), so
they compose in a single CI step.

## Why

The [evaluation](../../research/impeccable-design-skill.md) concluded "keep,
scoped": the detector is worth more than Impeccable's 24-command skill, because
it delivers its value at **zero always-on context cost** and composes into CI.
Loading the skill globally would reopen what the
[token-usage audit](../../research/token-usage-audit.md) closed.

Four drivers, all met without forking upstream:

| Driver | How it's met |
|---|---|
| Never contradict Casenote | One `ignoreRules` entry — the conflict is exactly one rule |
| Own the supply chain | `IMPECCABLE_BIN` + a pinned, hash-verified binary; nothing fetched at run time |
| Rules should be mine | `casenote_lint.py` encodes Casenote's own denylist |
| Insulate from upstream churn | Version bumps are a deliberate edit to `engine.lock.json` |

## Authority model

The rule that keeps this from overlapping what already exists:

| Concern | Owner |
|---|---|
| Colour, type, layout **token values** | `brands/alan-personal.md` (brand-guidelines skill) |
| Chart form — palettes, stat tiles, legends | `dataviz` |
| Casenote **denylist** enforcement | `casenote_lint.py` (here) |
| Generic slop rules | vendored Impeccable, minus suppressed conflicts |

`brands/alan-personal.md` stays the **single source of truth**. Every rule here
carries a `source` field naming the brand-file section it encodes, so a brand
change that invalidates a rule is greppable:

```bash
python3 -c "import sys;sys.path.insert(0,'scripts');import casenote_lint as c;\
[print(f'{k:26} {v.source}') for k,v in sorted(c.RULES.items())]"
```

> **Known gap.** `brands/alan-personal.md` cites
> `dataviz/scripts/validate_palette.js` as the validator that caught two
> invisible palette defects. That script is not resolvable on disk — `dataviz`
> appears to be a managed skill whose files are not local. Casenote's only other
> mechanical hook is therefore unreachable from a shell, which makes
> `casenote_lint.py` currently the only runnable check.

## Reproducing it

```bash
# 1. Resolve and verify the pinned engine (one time per machine)
python3 scripts/fetch_engine.py
eval "$(python3 scripts/fetch_engine.py --print-export)"

# 2. Copy the config into the project being linted
mkdir -p <project>/.impeccable && cp config/impeccable.json <project>/.impeccable/config.json

# 3. Run both checks
"$IMPECCABLE_BIN" detect <project>/src
python3 scripts/casenote_lint.py <project>/src
```

### Combined CI step

Note there is **no `set -e`**: both tools exit non-zero by design, and `set -e`
would abort on the very codes this needs to read.

```bash
#!/usr/bin/env bash
eval "$(python3 scripts/fetch_engine.py --print-export)" || exit 1

"$IMPECCABLE_BIN" detect --json src/ ; imp=$?
python3 scripts/casenote_lint.py --json src/ ; own=$?

if [ "$imp" -eq 1 ] || [ "$own" -eq 1 ]; then exit 1; fi   # unscannable
if [ "$imp" -eq 2 ] || [ "$own" -eq 2 ]; then exit 2; fi   # findings
exit 0
```

## The evidence this rests on

Every design decision here came from a probe, not from upstream's README. All
three are re-runnable:

```bash
# 1. The Casenote conflict is exactly ONE rule (overused-font, on Inter)
"$IMPECCABLE_BIN" detect scripts/fixtures/casenote_clean.html

# 2. DESIGN.md does NOT suppress detector findings — only config does.
#    Identical output with it present, absent, and under the control flag.
"$IMPECCABLE_BIN" detect --no-design-system scripts/fixtures/casenote_clean.html

# 3. The binary is a plain versioned npm package with a dist.integrity sha512
npm view @impeccable/cli-darwin-arm64@0.1.5 dist.integrity
```

Probe 2 is why there is no `DESIGN.md` generator here: an earlier draft of the
design was built on the assumption that it would defer to a local design system,
and it does not.

## Config

- `config/impeccable.json` — the `.impeccable/config.json` template. Suppresses
  `overused-font` and records why inline. Copy it, don't symlink it.
- `scripts/engine.lock.json` — committed provenance. The binary it describes is
  gitignored; re-run `fetch_engine.py` on a new machine to reproduce it.

Bumping the engine: edit `engine_version` in the lock, clear `binary_sha256`,
run `fetch_engine.py --print-sha256`, write the hash back, and commit. The
deliberateness is the point.

## Testing

```bash
cd scripts && uv run --with pytest python -m pytest -q    # 35 tests
```

`fixtures/casenote_clean.html` is the regression anchor and must stay at zero
findings. Each rule also has a counter-test in the legal direction — a sixth
series colour is fine, `--accent-bright` inside a gradient is fine, `light-dark()`
with no theme stamp is fine — because a design linter that cries wolf gets
ignored.

## Why both tools, concretely

Building this, the clean fixture was written by hand and looked right. Upstream's
detector found two real defects in it that `casenote_lint.py` did not:

- **`low-contrast`, 1.7:1.** The dark-mode blocks re-declared `--paper`, `--ink`,
  `--body` and `--accent` but not `--surface`, so `.card` kept the *light*
  surface under light-on-dark text.
- **`cramped-padding`.** A bordered, filled container with no inset.

The first is exactly the denylist item this repo's linter **cannot** encode
("overriding tokens on a container without re-declaring `color` and
`background`" — it needs cascade resolution, not text matching). Upstream caught
it by a different strategy entirely: computing the contrast ratio rather than
parsing the cascade.

That is the case for running both. They fail differently, and the gap in one is
covered by the other.

## Notes / known issues

- **Not for Harrison.ai repos** pending the supply-chain review noted in the
  evaluation. Personal projects only.
- **Not installed as a plugin, and no post-edit hook.** Both were deliberate
  cuts; see design.md's non-goals.
- **`casenote_lint.py` checks text, not the cascade.** An SVG sized from a
  stylesheet rule rather than its own tag is not caught, and "a second accent"
  and "teal and emerald as two series" are not encoded at all — they need
  judgment, and false positives train you to ignore the tool.
- The nine encoded rules are roughly half of Casenote's denylist. The other half
  is listed in design.md under "Deliberately not encoded", with reasons.
