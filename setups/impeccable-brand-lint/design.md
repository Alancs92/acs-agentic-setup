# Design: brand-agnostic design linting (vendored Impeccable + brand profiles)

- **Date:** 2026-09-19
- **Status:** implemented. **Revised 2026-09-20** — v1 was brand-bound; see
  "Revision: making it themeless" at the end of this file.
- **Origin:** [`../../research/impeccable-design-skill.md`](../../research/impeccable-design-skill.md)
  evaluated [Impeccable](https://github.com/pbakaus/impeccable) and concluded
  "keep, scoped": take the detector, leave the 24-command skill. This design is
  that conclusion made reproducible.

## Goal

Mechanical design enforcement that **composes with whatever brand is in force
instead of competing with it** — upstream Impeccable's generic slop rules plus a
brand's own denylist, both reporting through one exit-code contract, with **no
third-party binary fetched at run time** and **no forked upstream code to
maintain**.

The engine carries no brand's values. Universal rules are structural; brand
rules read their parameters from a profile under `brands/`. Casenote is the
first profile, not the subject.

Four drivers, from the evaluation:

1. Never contradict `brand-guidelines` / Casenote.
2. Own the supply chain — no surprise binary from someone else's release.
3. The rule set should encode *Casenote's* denylist, not just suppress upstream's.
4. Insulate from upstream churn (v4.x, pushed daily).

Non-goals (explicit YAGNI cuts):

- **No fork of `pbakaus/impeccable`.** §"Evidence" shows all four drivers are
  reachable without one. A 370MB Rust codebase merged forever against a
  daily-moving upstream buys only a single unified report.
- **No `/impeccable` skill, no 24 commands, no `PRODUCT.md`, no `init`.** The
  [token-usage audit](../../research/token-usage-audit.md) found plugin breadth
  is the dominant per-turn context tax. A detector costs zero context until invoked.
- **No `DESIGN.md` generator.** Proven inert for the detector — see Evidence 2.
- **No post-edit hook.** Latency and findings tokens on every UI file touch;
  `detect` runs manually or in CI instead.

## Evidence

All three probes run 2026-09-19 against `impeccable@4.1.0`, reproducible from
the fixture described in §Testing.

### 1. The Casenote conflict is exactly one rule

A fixture using Casenote's tokens, Inter/Source Serif 4/JetBrains Mono, and the
teal→emerald hairline produces exactly one finding:

```
[overused-font] Primary font: inter
```

Nothing else in Casenote trips upstream. The conflict is narrow and nameable.

### 2. `DESIGN.md` does not suppress detector findings

An earlier draft of this design proposed generating `DESIGN.md` from
`brands/alan-personal.md` so the brand file stayed the single owner. **That seam
does not exist.** Output was byte-identical with `DESIGN.md` present, absent,
and under the `--no-design-system` control. `DESIGN.md` informs the AI commands,
not the detector.

Suppression works only through `.impeccable/config.json`:

```jsonc
{ "detector": { "ignoreRules": ["overused-font"] } }
```

Verified: exit 0 with the config, finding returns under `--no-config`.

### 3. The binary can be owned without forking

The npm package is 20KB containing one 95-line shim. Its resolution order:

```
$IMPECCABLE_BIN → @impeccable/cli-<os>-<arch> → ~/.impeccable/bin/<version>/ → download
```

`IMPECCABLE_BIN` points the CLI at a binary we hold, so nothing is ever fetched
at run time. Driver 2 needs no fork.

**Two version numbers, not one.** The npm CLI package is `4.1.0`; the *engine*
is pinned separately in its `optionalDependencies` as `0.1.5`, and that is what
the release path `engine-v<version>/` uses. A lock file must record **both**.

**Preferred acquisition is the platform npm package, not the GitHub release.**
`@impeccable/cli-darwin-arm64@0.1.5` contains `package/bin/impeccable` directly
(5.9MB) and carries an npm `dist.integrity` sha512. npm versions are immutable
and the hash is enforced by `package-lock.json`, which is a stronger provenance
root than a mutable release asset plus a sidecar we verify ourselves. The
GitHub-release path stays documented as the fallback for a platform npm does not
publish, and upstream's own downloader fails closed on its sha256 sidecar
("refusing the unverified download") if it is ever used.

`IMPECCABLE_DOWNLOAD_BASE` additionally allows a private mirror if ever wanted.

## Authority model

The rule that prevents the overlap the evaluation warned about:

| Concern | Owner | Everything else defers by |
| --- | --- | --- |
| Colour, type, layout **token values** | `brands/alan-personal.md` | Citing it; never restating a hex |
| Chart form — palettes, stat tiles, legends | `dataviz` | Casenote already delegates validation to it |
| Casenote **denylist** enforcement | `casenote-lint` (this setup) | Rules cite the brand file section they encode |
| Generic slop rules | vendored Impeccable | Suppressed per-rule where Casenote disagrees |

`brands/alan-personal.md` stays the **single source of truth** and is still
edited only via `skill-improver`, per its own header. This setup adds no second
owner: `casenote-lint` rules carry a `source:` field naming the brand-file line
they encode, so a brand change that invalidates a rule is findable.

`brand-guidelines/SKILL.md` gains **one pointer line** (via `skill-improver`)
noting that Casenote's denylist has a mechanical checker and where it lives.

> **Known gap.** `brands/alan-personal.md` cites
> `dataviz/scripts/validate_palette.js` as the validator that caught two
> invisible palette defects. That script is not resolvable on disk — `dataviz`
> appears to be a managed skill whose files are not local. Casenote's only
> existing mechanical hook is therefore unreachable from a shell or CI. This
> setup does not replace it (palette validation is genuinely dataviz's job) but
> it does mean `casenote-lint` is, for now, the only runnable check.

## Architecture

Four pieces, each independently usable:

```
setups/impeccable-brand-lint/
  README.md              what/why/how to reproduce
  INDEX.md               navigation contract
  design.md              this file
  brands/
    INDEX.md
    casenote.json        one profile per brand; add a brand here, not in scripts/
  scripts/
    INDEX.md
    fetch_engine.py      one-time verified fetch → vendored binary + recorded hash
    brand_lint.py        the linter: 5 universal rules + 4 profile-driven
    test_brand_lint.py
    engine.lock.json     pinned CLI + engine versions, sha256, provenance
```

### `fetch_engine.py`

Resolves the pinned engine to a local binary and records provenance in
`engine.lock.json` (CLI version, engine version, platform, sha256 of the binary,
acquisition route, date).

Primary route: install the pinned platform package
(`@impeccable/cli-<os>-<arch>@<engine-version>`), whose npm `dist.integrity`
sha512 is verified by npm itself, then hash the extracted binary and record it.
Fallback route (platform npm does not publish): the GitHub release asset plus its
`.sha256` sidecar, failing closed exactly as upstream's shim does.

Re-running **verifies the existing binary against the lock** rather than
re-acquiring. A version bump is a deliberate edit to the lock plus a reviewed
commit — this is what satisfies driver 4.

Emits the `IMPECCABLE_BIN` export line for a shell profile or CI step.

### `brand_lint.py`

Encodes the mechanically-testable half of a brand's denylist. Five rules are
universal; four read their values from the `--brand` profile and return nothing
when unconfigured. Each rule carries an id, a severity, and where its values
come from (`brand_lint.py --list-rules` prints this).

Rule ids below are the current, brand-neutral ones; the "Brand-file source"
column describes Casenote, the first profile.

| Rule id | Catches | Brand-file source |
| --- | --- | --- |
| `light-dark-with-theme-stamp` (universal) | `light-dark()` used where a `data-theme` stamp must win | Denylist |
| `theme-on-root` (universal) | `data-theme` written onto the document root | Denylist |
| `svg-no-min-width` (universal) | fixed-`viewBox` SVG at `width:100%` with no `min-width` | Denylist / Infographics |
| `raw-hex` (universal) | colour literal outside a `:root` / theme block | Denylist |
| `forbidden-token-names` (brand) | `--swatch-*`, `--life-*`, `--dot-twinkle` | Denylist |
| `pure-black-on-white` (universal) | `#000` body copy on `#fff` | Denylist |
| `gradient-only-as-mark` (brand) | `--accent-bright` / `#10b981` as a mark on light ground | Colors (2.49:1) |
| `status-as-series` (brand) | `--good` / `--warning` / `--critical` used as a series colour | Status colours |
| `series-ceiling` (brand) | a 7th categorical colour | Series palette |

Deliberately **not** encoded — these need judgment, and a false positive on a
design rule trains you to ignore the tool:

- "A second accent" — requires intent, not occurrence counting.
- "Teal and emerald as two series colours" — needs to know which two of many
  declared colours are actually *the* series.
- "Overriding tokens on a container without re-declaring `color` and
  `background`" — needs cascade resolution, not text matching.
- "A webfont link in a deck or a document" — medium is not inferable from the file.

### Exit-code contract

`brand_lint.py` matches Impeccable's contract so the two compose in one CI step:

- `0` — no findings
- `1` — a target could not be scanned (operational failure; takes precedence)
- `2` — findings

`--json` emits the same finding shape upstream uses (`rule`, `severity`, `file`,
`line`, `snippet`, `description`) so both tools' output can be concatenated.

## Data flow

```
  brands/<brand>.md ──sha256──> brands/<brand>.json ──parameters──> brand_lint.py
        (source of truth)          (profile)          │                  │
                                        │             └──ignoreRules─┐   │
  engine.lock.json ──verifies──> binary │──$IMPECCABLE_BIN──> impeccable detect
                                        │                             │   │
                                        └──> .impeccable/config.json ─┘   │
                                                             ▼            ▼
                                                        exit 0 / 1 / 2 (max of both)
```

## Error handling & safety

- `fetch_engine.py` **fails closed**: no sidecar, empty sidecar, or hash
  mismatch aborts without writing. Mirrors upstream's own posture.
- The vendored binary is never committed to git (platform-specific, large); the
  **lock file is**, so any machine can reproduce and verify the same engine.
- `brand_lint.py` reads only; it never rewrites source.
- An unparseable file is a `1`, never a silent `0`.

## Testing

TDD, per the repo's existing suites (`uv run --with pytest`):

- A **positive fixture** per rule — the Casenote-conformant probe from Evidence 1,
  which must stay at zero findings from `brand_lint` under its own profile.
- A **negative fixture** per rule, each violating exactly one denylist item.
- Exit-code tests: 0 clean, 2 on findings, 1 on an unreadable target.
- A lock-file test: a tampered binary fails verification.
- `--json` shape test asserting field parity with upstream's finding shape.

The Evidence probes are themselves re-runnable and belong in the README as the
reproduction steps, since every claim in this design rests on them.

## Repo placement

`setups/impeccable-brand-lint/` per the repo's self-contained-setup convention;
`INDEX.md` added in the same commit and linked from `setups/INDEX.md`;
`CHANGELOG.md` entry on landing. The research note's status flips to
`concluded → promoted to setups/impeccable-brand-lint`.

## Open questions

1. **Where does the resolved binary live?** Three candidates: `node_modules/`
   from the pinned platform package (simplest, but per-project and 5.9MB each),
   `~/.impeccable/bin/<engine-version>/` reusing upstream's own cache path (so a
   bare `npx impeccable` finds it too), or a path inside this repo (obviously
   ours, duplicated per worktree). Leaning toward upstream's cache path, with
   `engine.lock.json` as the committed provenance record and `IMPECCABLE_BIN`
   pointing at it.
2. **Per-project config distribution.** `config/impeccable.json` is a template to
   copy. Whether that copy ever becomes a script depends on how many projects
   actually adopt this — one project does not justify a generator.


---

## Revision: making it themeless (2026-09-20)

### What v1 got wrong

v1 shipped as `impeccable-casenote`: one brand's hexes (`#10b981`), token names
(`--accent-bright`, `--series-N`, `--good`) and `ignoreRules` were written
directly into the rule functions and the config template. It worked, and it was
the wrong shape.

Impeccable itself is brand-agnostic. The `brand-guidelines` skill is already
multi-brand — a router plus independent `brands/*.md` subsets for Casenote,
Harrison.ai and Anthropic. v1's wrapper was the only brand-bound thing in the
stack, and it would have needed a fork per brand.

Measuring the coupling showed a clean seam: **five rules were already universal**
and **four needed values, not different logic**.

| Universal | Brand-parameterised |
|---|---|
| `raw-hex` | `forbidden-token-names` (was `site-token-names`) |
| `pure-black-on-white` | `gradient-only-as-mark` (was `accent-bright-as-mark`) |
| `light-dark-with-theme-stamp` | `status-as-series` |
| `theme-on-root` | `series-ceiling` (was `seventh-series-colour`) |
| `svg-no-min-width` | |

### The shape now

- Rules take `(text, path, profile)`. Universal ones ignore the profile.
- A brand rule with nothing configured returns **no findings** — it never falls
  back to a default brand, so an unconfigured run is honest rather than
  accidentally Casenote-shaped.
- `brands/<brand>.json` holds only machine-checkable parameters.
- The same profile emits the detector's `ignoreRules` via
  `--emit-impeccable-config`, so one brand drives **both** tools. In v1 that
  config was a separate hand-maintained file that could silently disagree.

### Sync: hand-authored profile + staleness hash

Considered and rejected: **deriving** profiles by parsing the brand markdown.
The three brand files have different section shapes — Casenote has token tables,
`harrison-ai.md` and `anthropic.md` do not — so a parser tuned to one returns an
empty profile for the others, and an empty profile reads as "clean". A silent
false negative in a linter is worse than no linter.

Also rejected for now: a machine-readable JSON block inside each `brands/*.md`.
Cleanest long-term and worth revisiting, but `brand-guidelines` is
`skill-improver`-only by its own header, so it needs its own pass across three
files before anything works.

What shipped: the profile records `source_sha256` of the brand markdown and the
linter refuses to run on drift (exit 1), naming both hashes. Explicit, not magic.

### What proves it

A second synthetic brand (`Acme`) in the test suite, with token names that
overlap Casenote's nowhere. The suite asserts Acme's violations are caught *and*
that Casenote's tokens are invisible under the Acme profile. Without that second
profile, every test would still pass on a Casenote-shaped engine.
