import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import casenote_lint

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).with_name("casenote_lint.py")


def run_cli(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class TestCore(unittest.TestCase):
    def test_clean_fixture_has_no_findings(self):
        path = FIXTURES / "casenote_clean.html"
        findings = casenote_lint.scan_text(path.read_text(), str(path))
        self.assertEqual(findings, [], f"clean fixture regressed: {findings}")

    def test_every_rule_declares_a_source(self):
        for rule_id, fn in casenote_lint.RULES.items():
            self.assertTrue(getattr(fn, "source", ""),
                            f"{rule_id} has no brand-file source")

    def test_scan_paths_reports_unreadable(self):
        findings, unreadable = casenote_lint.scan_paths([Path("/nonexistent.html")])
        self.assertEqual(findings, [])
        self.assertEqual(len(unreadable), 1)


class TestExitCodes(unittest.TestCase):
    def test_clean_exits_zero(self):
        code, _, _ = run_cli(str(FIXTURES / "casenote_clean.html"))
        self.assertEqual(code, 0)

    def test_unreadable_exits_one(self):
        code, _, _ = run_cli("/nonexistent.html")
        self.assertEqual(code, 1)

    def test_unreadable_takes_precedence_over_findings(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, _, _ = run_cli(str(bad), "/nonexistent.html")
            self.assertEqual(code, 1, "operational failure must beat findings")


class TestJsonShape(unittest.TestCase):
    def test_json_fields_match_upstream_shape(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, out, _ = run_cli("--json", str(bad))
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertGreaterEqual(len(payload), 1)
            self.assertEqual(
                set(payload[0]),
                {"rule", "severity", "file", "line", "snippet", "description", "source"},
            )


if __name__ == "__main__":
    unittest.main()


class TestTokenRules(unittest.TestCase):
    def assert_flags(self, rule_id, fixture_name):
        path = FIXTURES / fixture_name
        findings = casenote_lint.scan_text(path.read_text(), str(path))
        ids = [f.rule for f in findings]
        self.assertIn(rule_id, ids, f"{fixture_name} did not trip {rule_id}: {ids}")

    def test_site_token_names(self):
        self.assert_flags("site-token-names", "site_tokens_bad.html")

    def test_raw_hex_outside_root(self):
        self.assert_flags("raw-hex", "raw_hex_bad.html")

    def test_pure_black_on_white(self):
        self.assert_flags("pure-black-on-white", "pure_black_bad.html")

    def test_accent_bright_as_mark(self):
        self.assert_flags("accent-bright-as-mark", "accent_bright_bad.html")

    def test_status_as_series(self):
        self.assert_flags("status-as-series", "status_series_bad.html")

    def test_seventh_series_colour(self):
        self.assert_flags("seventh-series-colour", "seventh_series_bad.html")

    def test_sixth_series_colour_is_legal(self):
        """Six categories is the ceiling; the seventh is the violation."""
        text = "<style>:root{--series-1:#0d9488;--series-6:#77712f;}</style>"
        ids = [f.rule for f in casenote_lint.scan_text(text, "x.html")]
        self.assertNotIn("seventh-series-colour", ids)

    def test_accent_bright_in_gradient_is_legal(self):
        """--accent-bright is the gradient partner; that use is correct."""
        text = ("<style>.hairline{background:linear-gradient(90deg,"
                "var(--accent),var(--accent-bright));}</style>")
        ids = [f.rule for f in casenote_lint.scan_text(text, "x.html")]
        self.assertNotIn("accent-bright-as-mark", ids)
