# setups/impeccable-brand-lint/scripts/fixtures/

Test fixtures for `brand_lint.py`.

- `casenote_clean.html` — Casenote-conformant specimen, linted under the
  Casenote profile. **Must stay at zero findings.** If a new rule trips it, the
  rule is wrong, not the fixture.
- `*_bad.html` — one per rule, each violating exactly one item so a failure
  names the rule that regressed. The brand-parameterised ones are written in
  Casenote's vocabulary and are only findings under that profile.

The synthetic `Acme` brand used to prove brand-agnosticism lives inline in
`test_brand_lint.py` rather than here — it is a profile, not a document.
