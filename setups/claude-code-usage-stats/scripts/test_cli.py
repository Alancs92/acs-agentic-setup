# scripts/test_cli.py
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("collect_stats.py")
TS = 1786507331000


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        acct = self.store / "solo"
        (acct / "projects").mkdir(parents=True)
        (acct / "history.jsonl").write_text(json.dumps({
            "display": "/code-review go", "timestamp": TS,
            "project": "~/repos/x", "sessionId": "s1", "pastedContents": {}}) + "\n")
        (self.tmp / "canonical").mkdir()
        self.out = self.tmp / "out"

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(self.store),
             "--canonical", str(self.tmp / "canonical"), "--out", str(self.out), *extra],
            capture_output=True, text=True)

    def test_writes_both_artifacts_and_exits_zero(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertEqual(stats["schema_version"], 1)
        self.assertEqual(stats["totals"]["sum_of_totals"], 1)
        html = (self.out / "dashboard.html").read_text()
        self.assertIn('id="stats-data"', html)

    def test_json_only_skips_html(self):
        r = self.run_cli("--json-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.out / "stats.json").exists())
        self.assertFalse((self.out / "dashboard.html").exists())

    def test_second_run_uses_cache(self):
        self.run_cli()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_cache_flag_writes_no_cache_file(self):
        self.run_cli("--no-cache")
        self.assertFalse((self.out / "cache.json").exists())

    def test_accounts_filter(self):
        r = self.run_cli("--accounts", "nosuchaccount")
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertEqual(stats["accounts"], [])


if __name__ == "__main__":
    unittest.main()
