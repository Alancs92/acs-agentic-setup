# setups/impeccable-casenote/scripts/fixtures/

Test fixtures for `casenote_lint.py`.

- `casenote_clean.html` — Casenote-conformant specimen. **Must stay at zero
  findings.** If a new rule trips it, the rule is wrong, not the fixture.
- `*_bad.html` — one per rule, each violating exactly one denylist item so a
  failure names the rule that regressed.
