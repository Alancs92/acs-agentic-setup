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

    def run_with_store(self, store):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--store", store,
             "--canonical", str(self.tmp / "canonical"), "--out", str(self.out)],
            capture_output=True, text=True, cwd=self.tmp)

    def seed_previous_output(self):
        self.out.mkdir(parents=True)
        (self.out / "stats.json").write_text("previous")
        (self.out / "dashboard.html").write_text("previous")

    def assert_rejected_and_untouched(self, r):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--store", r.stderr)
        self.assertEqual((self.out / "stats.json").read_text(), "previous")
        self.assertEqual((self.out / "dashboard.html").read_text(), "previous")

    def test_empty_store_is_rejected_not_read_as_cwd(self):
        # An unset shell variable expands to --store "", which Path() reads as
        # the current directory -- the run then treats every repo as an account.
        self.seed_previous_output()
        self.assert_rejected_and_untouched(self.run_with_store(""))

    def test_missing_store_is_rejected(self):
        self.seed_previous_output()
        self.assert_rejected_and_untouched(self.run_with_store(str(self.tmp / "nope")))

    def test_store_without_accounts_is_rejected(self):
        self.seed_previous_output()
        empty = self.tmp / "empty-store"
        empty.mkdir()
        self.assert_rejected_and_untouched(self.run_with_store(str(empty)))

    def test_no_temp_files_left_behind(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["cache.json", "dashboard.html", "stats.json"])


if __name__ == "__main__":
    unittest.main()
