<!--
Evaluation of a third-party design skill. Promotes into a setup only once
it's actually installed and used on a real project — see "Conclusion".
Token-budget reasoning here defers to research/token-usage-audit.md; don't
restate the lean-profile findings, link them.
-->

# Impeccable — third-party design skill for coding agents

- **Date:** 2026-09-18
- **Status:** concluded → promoted to
  [`setups/impeccable-casenote`](../setups/impeccable-casenote/README.md)

## Question

[Impeccable](https://github.com/pbakaus/impeccable) is a design-language skill
for AI coding agents. Does it earn a place in any of my setups, and if so
which lane — always-on personal profile, per-project, or CI-only? The
[token-usage audit](token-usage-audit.md) concluded plugin breadth is the
dominant per-turn context tax, so anything added has to justify its slot.

## Findings

### What it is

Descends from Anthropic's `frontend-design` skill. Premise: models trained on
the same SaaS templates converge on the same visuals (purple→blue gradients,
Inter everywhere, nested cards), and that sameness is now a legible tell.
Three parts:

- **`PRODUCT.md` / `DESIGN.md`** — durable product + design truth (audience,
  purpose, constraints, voice), authored by `/impeccable init`.
- **Detector engine** — anti-pattern rules in a self-contained Rust binary.
  Deterministic, no model call.
- **Commands** — `audit`, `critique`, `polish`, `distill`, `animate`, `live`,
  and others, routed through one `/impeccable` skill.

The design choice worth stealing regardless of adoption: rules that are
*mechanically* checkable (border widths, easing curve names, font stacks,
contrast ratios) are promoted out of prose guidance into a binary with real
exit codes. Same shape as the global "mechanical enforcement" principle,
applied to visual design.

### Verified directly (2026-09-18)

- Apache-2.0, primary language JavaScript, created 2025-11-16, pushed the same
  day this note was written — actively maintained, not a spike.
- 68.8k stars / 4.2k forks.
- Ships `.claude-plugin/marketplace.json` (plugin v4.3.1, `source: ./plugin`),
  so it installs as a normal Claude Code plugin. **No need for
  `npx impeccable install`**, which is the path that mutates dotfiles and
  writes provider-specific skill copies into `.claude/`, `.cursor/`, `.github/`.
- `npx impeccable detect --json <path>` works standalone with no install, no
  agent, and no API key. Exit 2 on findings, 0 clean, 1 scan failure.

### Taken from the project's own README, not independently verified

61 detector rules; 24 commands; engine downloads a prebuilt Rust binary to
`~/.impeccable/bin/` on first run; 17+ supported harnesses; optional post-edit
hook in `.claude/settings.local.json` that runs the detector on UI file edits.

### Detector run against this repo

Only real UI artifact here is the usage-stats dashboard
([`../setups/claude-code-usage-stats/dashboard/template.html`](../setups/claude-code-usage-stats/dashboard/template.html),
1138 lines, hand-written, standalone).

```bash
npx impeccable detect --json setups/claude-code-usage-stats/dashboard/template.html
# exit 2 — 1 finding
```

```
[side-tab] warning / slop — "Side-tab accent border"
  border-left: 3px + border-radius: 6px
  → Thick colored border on one side of a card — the most recognizable
    tell of AI-generated UIs.
```

Anchored to `.utc-note` (`template.html:81-85`): `border-left: 3px solid
var(--accent)` combined with `border-radius: 6px`. The `.stat` rules at
`:140`/`:143` use 2px and were **not** flagged — the rule is thresholded, not
boolean, and correlates two properties on one selector rather than matching a
token (hence `"line": 0` in the JSON: there's no single line to point at).

Read on the result: one warning across 1138 hand-written lines is a good
report card for the dashboard and only a modest signal for the tool. It did
surface something neither I nor the agent would have independently named,
which is the whole value proposition — but the rule set is narrow and
slop-focused, not an a11y or contrast audit.

### Conflicts to resolve before any install

1. **Context budget.** A 1-skill/24-command plugin loaded always-on directly
   reopens what [`token-usage-audit.md`](token-usage-audit.md) closed
   (−8.7k/−20% startup tax by pruning to a lean core). Per-project enablement
   only, in repos that actually have UI.
2. **Authority overlap.** `dataviz` already owns chart palettes, stat tiles,
   legends and dashboard layout; `brand-guidelines` owns Casenote/HAI/Anthropic
   color, type and logo rules. Impeccable's `PRODUCT.md`/`DESIGN.md` claim the
   same ground. Needs an explicit precedence rule before it's loaded alongside
   them — provisionally: brand-guidelines wins on color/type tokens, dataviz
   wins on chart form, impeccable audits the remainder and owns the detector.
3. **Per-edit hook cost.** The optional post-edit hook spends latency and
   findings tokens on every UI file touch. Leave it off; run `detect` manually
   or in CI.
4. **Supply chain.** The engine is a prebuilt binary fetched on first run from
   a third party. Acceptable for personal projects. **Not** to be introduced
   into Harrison.ai repos without review.

## Conclusion / next step

Keep, scoped. The detector is worth more than the skill: it delivers the value
with zero always-on context cost and composes into CI as a gate
(`npx impeccable detect --json . ; exit 2`). The 24-command skill is the part
that has to earn its slot, and it can only do that on a project with sustained
UI work.

Planned progression, promote to `setups/<slug>` at step 2 or 3:

1. **Now** — detector only, invoked ad hoc. No install, no plugin, no hook.
2. **Next** — on a personal project with real UI, install via the plugin
   marketplace (`pbakaus/impeccable`), enabled for that project only, run
   `/impeccable init` to produce `PRODUCT.md`, and settle the precedence rule
   against `dataviz` + `brand-guidelines` in practice.
3. **If it holds** — write it up as a reproducible setup and wire `detect`
   into that project's CI.

Not adopted for Harrison.ai work pending the supply-chain review in conflict 4.

### Update 2026-09-19 — design landed

Step 2 was designed in
[`../setups/impeccable-casenote/design.md`](../setups/impeccable-casenote/design.md),
and building it corrected two things asserted above from the project's README:

- The engine version (`0.1.5`, in the CLI package's `optionalDependencies`) is
  **not** the npm CLI version (`4.1.0`). The release path uses the former.
- The binary is also published as a plain versioned npm package
  (`@impeccable/cli-<os>-<arch>`) with an npm `dist.integrity` sha512 — a better
  provenance root than the GitHub release asset, and the reason no bespoke
  downloader is needed.

Also disproven while designing: `DESIGN.md` does **not** suppress detector
findings. Only `.impeccable/config.json` → `detector.ignoreRules` does.

Open item found while running this, unrelated to Impeccable: the
`claude-code-usage-stats` setup has no `CHANGELOG.md` entry yet.
