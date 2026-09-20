# Usage-dashboard theme

> **Version 1 · 2026-09-20 · Owner: Alan.** The design system of
> `dashboard/template.html`. This file is the source of truth; the template
> implements it. It is **not** Casenote and not Harrison.ai — the dashboard is a
> dense data surface with its own requirements, and borrowing an artifact brand
> would cost contrast it cannot spare.
>
> Linted by `setups/impeccable-brand-lint` under the `usage-dashboard` profile,
> which records this file's sha256. Change the palette here and in the template
> together, then refresh the hash — the linter will tell you if you forget.

## What this theme is for

Thirteen panels of inline SVG on one standalone page, opened over `file://` with
no network. Every colour has to survive: eight categorical series on one chart, a
sequential heat scale, light and dark ground, and a reader scanning for outliers
rather than reading prose.

## Colors

Contrast is the WCAG ratio against `--panel` in that mode, computed 2026-09-20.
Text floor 4.5:1, non-text mark floor 3:1.

| Token | Light | Dark | Role | Contrast L / D |
| --- | --- | --- | --- | --- |
| `--bg` | `#f6f5f2` | `#16171a` | page ground | — |
| `--panel` | `#ffffff` | `#1e2024` | panel fill | — |
| `--ink` | `#1c1b18` | `#e9e7e2` | headings, values | 17.22 / 13.20 |
| `--muted` | `#66645c` | `#9a9791` | axis labels, metadata | 5.93 / 5.60 |
| `--line` | `#e3e0d9` | `#2f3237` | borders | — |
| `--grid` | `#ece9e2` | `#2a2d32` | chart gridlines | — |
| `--chip` | `#efeee9` | `#282b30` | chips, tags | — |
| `--accent` | `#2f6690` | `#7fb3d5` | links, the single emphasis | 6.13 / 7.23 |

**Neutrals are warm-tinted, never pure.** `--ink` is `#1c1b18`, not `#000`;
dark ground is `#16171a`, not black. The warmth is what stops thirteen grey
panels reading as a spreadsheet.

### Series palette — eight categorical

Eight is the ceiling and it is already generous; past eight, facet.

| # | Light | Dark | vs panel L / D |
| - | ----- | ---- | -------------- |
| `--s1` | `#2f6690` | `#7fb3d5` | 6.13 / 7.23 |
| `--s2` | `#b8613a` | `#e08a5c` | 4.37 / 6.18 |
| `--s3` | `#3f7d54` | `#6fc08a` | 4.91 / 7.44 |
| `--s4` | `#7a5391` | `#b691d6` | 6.05 / 6.22 |
| `--s5` | `#a2495f` | `#e0808f` | 5.75 / 5.95 |
| `--s6` | `#77712f` | `#c4bc63` | 5.01 / 8.33 |
| `--s7` | `#2f7f80` | `#5fc0c1` | 4.70 / 7.61 |
| `--s8` | `#8a5a2b` | `#c9945c` | 5.87 / 6.12 |

Every series clears **4.37:1 in the worst case** — comfortably past the 3:1 mark
floor, and high enough to carry a label directly.

> **`--s1` and `--accent` are the same value.** Deliberate: the accent is the
> first series, so a single-series chart reads as "the emphasis colour" rather
> than introducing a ninth hue. It does mean a chart using `--s1` alongside a
> link has two things in one colour — acceptable here because links live in
> prose and series live in SVG, never side by side.

### Heat scale — sequential, not categorical

`--heat0` `#eae8e2` → `--heat1` `#c9dcea` → `--heat2` `#8fb9d6` →
`--heat3` `#5590b9` → `--heat4` `#23577f` (dark: `#24262a` → `#24435a` →
`#2f6188` → `#4a86b0` → `#8fc2e2`).

Ordered lightness ramp for the calendar heatmap. **Never a series colour**, and
never used for categories — a sequential scheme implies magnitude.

### Warn triad — reserved

`--warn-bg` `#fbeedd` · `--warn-ink` `#8a4a10` · `--warn-line` `#e6c79a`
(dark: `#3a2a14` / `#f0c48a` / `#6a4c20`). Ink-on-bg measures 5.99 light,
8.52 dark. Always paired with a label, never colour alone, and **never a series
colour**.

## NOT this theme

- **A ninth categorical colour.** Eight is the ceiling; facet instead.
- **The heat scale or the warn triad used as series colours.**
- **A raw hex outside the `:root` / dark blocks.** Colour comes from tokens.
- **Pure `#000` on `#fff`.** `--ink` on `--panel` is the pairing.
- **Another system's token names** — Casenote's `--series-N`, `--paper`,
  `--accent-bright`, or the site's `--swatch-N` / `--life-N` / `--dot-twinkle`.
  Their presence means something was pasted in from a different design system.
- **`light-dark()`.** The page ships a `data-theme` toggle, which `light-dark()`
  cannot see; it compiles and silently ignores it. Tokens are declared three
  times instead.
- **`data-theme` on the document root.** Scope it to a container.
- **A fixed-`viewBox` SVG at `width:100%` with no `min-width`.** Axis labels
  scale with the container and go illegible on a phone.
- **A thick one-sided accent border** (`border-left: 3px` with a radius). It
  reads as an AI-generated callout; the panel fill and a 1px border already
  separate.
- **A webfont.** The page must open over `file://` with no network. System
  stack only.
- **A chart library or any external request.** Inline SVG, vanilla JS.

## Typography

System stack only — no webfont link, because the page is opened from disk and
may be viewed offline.

```css
font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;  /* .mono, code */
```

Headings and body use the browser's default UI stack. `h2` is the panel
eyebrow: 13px, uppercase, `letter-spacing: 0.06em`, `--muted`. Numerals that
line up get `font-variant-numeric: tabular-nums`.

## Changelog

- **2026-09-20 · v1.** First written down. The palette already existed in
  `dashboard/template.html`; this file documents it, records measured contrast
  for every token, and states the denylist so it can be mechanically enforced.
  No colour values changed. The `--s1`/`--accent` collision is documented as
  deliberate rather than corrected.
