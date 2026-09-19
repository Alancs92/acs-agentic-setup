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
