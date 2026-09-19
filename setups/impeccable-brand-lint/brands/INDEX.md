# setups/impeccable-brand-lint/brands/

One JSON profile per brand. A profile holds only the **machine-checkable
parameters** of a brand — forbidden token patterns, gradient-only colours,
status tokens, the categorical ceiling, and which upstream Impeccable rules that
brand legitimately disagrees with.

The brand's actual definition stays in the `brand-guidelines` skill. A profile
never restates a value that file owns beyond what a checker needs, and records
that file's `source_sha256` so drift is detectable rather than silent.

| Profile | Brand | Source of truth |
|---|---|---|
| [`casenote.json`](casenote.json) | Casenote (Alan's personal artifact brand) | `brand-guidelines/brands/alan-personal.md` |

## Adding a brand

1. Copy `casenote.json`, set `name`, `source_file` and the parameters.
2. Record the source hash:
   `python3 -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('<brand>.md').read_text().encode()).hexdigest())"`
3. Add a row above. Nothing in `scripts/` changes — the engine holds no brand's values.

A brand whose markdown is not on this machine can omit `source_sha256`; the
staleness check then skips it.
