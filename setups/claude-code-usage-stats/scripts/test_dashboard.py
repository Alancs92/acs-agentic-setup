# scripts/test_dashboard.py
"""Structural contracts for dashboard/template.html.

The dashboard is HTML+JS, so these assert the contracts that actually break in
practice -- a missing injection placeholder, a dropped version guard, a CDN
sneaking in, a session duration being computed -- rather than pixels.
"""
import re
import unittest
from pathlib import Path

import render

TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"

EMPTY_STATS = {
    "schema_version": 1,
    "accounts": [],
    "prompt_buckets": [],
    "token_buckets": [],
    "sessions": [],
    "projects": [],
    "tools": [],
    "slash": [],
    "branches": [],
    "versions": [],
    "session_histogram": {},
    "totals": {"union": 0, "sum_of_totals": 0},
    "run": {},
    "window": {},
    "timezone": "UTC",
    "generated_at": "2026-08-22T00:00:00Z",
}


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text()

    def test_has_injection_placeholder(self):
        self.assertIn(render.PLACEHOLDER, self.text)
        self.assertEqual(self.text.count(render.PLACEHOLDER), 1)

    def test_no_external_requests(self):
        """No CDN, no remote anything -- the page must work offline forever.

        The SVG namespace URI (http://www.w3.org/...) is the one allowed match:
        it is an identifier passed to createElementNS, never fetched.
        """
        for pattern in (r"https?://(?!www\.w3\.org)", r"<script[^>]+src=",
                        r"<link[^>]+href=[\"']http", r"fetch\s*\(",
                        r"XMLHttpRequest", r"@import"):
            self.assertIsNone(re.search(pattern, self.text),
                              "external ref matched %s" % pattern)

    def test_guards_schema_version(self):
        self.assertIn("schema_version", self.text)
        self.assertRegex(self.text, r"schema_version\s*!==?\s*1")

    def test_never_computes_session_duration(self):
        """last_ts minus first_ts would present resume artefacts as work sessions.

        `run.duration_seconds` is the COLLECTOR's own runtime, which the spec
        requires in the freshness footer, so that one token is allowed -- and
        nothing else containing "duration" is.
        """
        self.assertNotRegex(self.text, r"last_ts\s*[-+]")
        self.assertNotRegex(self.text, r"first_ts\s*[-+]")
        self.assertNotRegex(self.text, r"[-+]\s*(?:\w+\.)?first_ts")
        for match in re.findall(r"\w*duration\w*", self.text):
            self.assertEqual(match, "duration_seconds")

    def test_declares_required_panels(self):
        for panel in ["calendar", "prompts-per-day", "hour-weekday", "tokens",
                      "models", "tools", "projects", "sessions", "slash", "footer"]:
            self.assertIn('data-panel="%s"' % panel, self.text, panel)

    def test_has_account_filter_and_dedup_toggle(self):
        self.assertIn('id="account-filter"', self.text)
        self.assertIn('id="dedup-toggle"', self.text)

    def test_renders_with_real_stats_without_placeholder_left(self):
        out = render.render(self.text, EMPTY_STATS)
        self.assertNotIn(render.PLACEHOLDER, out)
        self.assertIn('id="stats-data"', out)


if __name__ == "__main__":
    unittest.main()
