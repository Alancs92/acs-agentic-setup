# scripts/test_aggregate.py
import json, tempfile, unittest
from pathlib import Path
import aggregate, discovery

TS = 1786507331000   # 2026-08-11T04:02:11Z


def hrow(ts, display, project="~/repos/x", session="s1"):
    return {"display": display, "timestamp": ts, "project": project,
            "sessionId": session, "pastedContents": {}}


def arow(mid, ts="2026-08-11T04:02:11.000Z", model="claude-sonnet-5"):
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1",
            "isSidechain": False, "cwd": "/repo", "gitBranch": "main",
            "version": "2.1.220",
            "message": {"id": mid, "model": model, "content": [{"type": "tool_use", "name": "Bash"}],
                        "usage": {"input_tokens": 5, "output_tokens": 7,
                                  "cache_read_input_tokens": 9, "cache_creation_input_tokens": 3}}}


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.canonical = self.tmp / "canonical"

        # account "a": 2 prompts (1 shared with b), 1 transcript
        a = self.store / "a"
        (a / "projects" / "proj").mkdir(parents=True)
        (a / "history.jsonl").write_text(
            json.dumps(hrow(TS, "shared")) + "\n" + json.dumps(hrow(TS, "a-only")) + "\n")
        (a / "projects" / "proj" / "s1.jsonl").write_text(
            json.dumps(arow("m1")) + "\n" + json.dumps(arow("m1")) + "\n")   # dup id

        # account "b": duplicate-only history, but a UNIQUE transcript
        b = self.store / "b"
        (b / "projects" / "proj").mkdir(parents=True)
        (b / "history.jsonl").write_text(json.dumps(hrow(TS, "shared")) + "\n")
        (b / "projects" / "proj" / "s2.jsonl").write_text(json.dumps(arow("m9")) + "\n")

        self.accounts = discovery.discover_accounts(self.store, self.canonical)

    def build(self):
        return aggregate.build_stats(self.accounts, store_label=str(self.store),
                                     timezone_name="Australia/Sydney")

    def test_schema_version_and_utc_marker(self):
        s = self.build()
        self.assertEqual(s["schema_version"], 1)
        self.assertEqual(s["timezone"], "Australia/Sydney")
        self.assertTrue(s["generated_at"].endswith("Z"))

    def test_three_figures_present_and_consistent(self):
        s = self.build()
        by = {a["name"]: a for a in s["accounts"]}
        self.assertEqual(by["a"]["prompts"], {"total": 2, "exclusive": 1, "shared": 1})
        self.assertEqual(by["b"]["prompts"], {"total": 1, "exclusive": 0, "shared": 1})
        self.assertEqual(s["totals"]["union"], 2)
        self.assertEqual(s["totals"]["sum_of_totals"], 3)

    def test_prompt_buckets_lossless(self):
        s = self.build()
        self.assertEqual(sum(r["n"] for r in s["prompt_buckets"]), s["totals"]["sum_of_totals"])
        self.assertLessEqual(len(s["prompt_buckets"]), s["totals"]["sum_of_totals"])

    def test_duplicate_history_account_still_contributes_tokens(self):
        """The whole point of 'dead is per-layer': b's history is a duplicate,
        but its transcript tokens are unique and must appear."""
        s = self.build()
        b_tokens = [t for t in s["token_buckets"] if t["a"] == "b"]
        self.assertEqual(len(b_tokens), 1)
        self.assertEqual(b_tokens[0]["in"], 5)

    def test_message_id_dedup_survives_aggregation(self):
        s = self.build()
        a_tokens = [t for t in s["token_buckets"] if t["a"] == "a"][0]
        self.assertEqual(a_tokens["in"], 5)     # not 10

    def test_no_duration_key_anywhere(self):
        blob = json.dumps(self.build())
        self.assertNotIn("duration", blob.replace('"duration_seconds"', ""))

    def test_histogram_agrees_with_session_rows(self):
        s = self.build()
        for acct, hist in s["session_histogram"].items():
            rows = [r for r in s["sessions"] if r["a"] == acct]
            rebuilt = {}
            for r in rows:
                k = str(r["prompts"])
                rebuilt[k] = rebuilt.get(k, 0) + 1
            self.assertEqual(hist, rebuilt, acct)

    def test_projects_rollup_matches_buckets(self):
        s = self.build()
        from collections import defaultdict
        derived = defaultdict(int)
        for r in s["prompt_buckets"]:
            derived[(r["a"], r["p"])] += r["n"]
        for row in s["projects"]:
            self.assertEqual(row["n"], derived[(row["a"], row["path"])])

    def test_no_per_prompt_row_emitted(self):
        s = self.build()
        for key in ("prompts_raw", "prompt_rows", "prompt_hashes"):
            self.assertNotIn(key, s)
        self.assertNotIn("a-only", json.dumps(s))   # no prompt text leaks

    def test_run_block_reports_counters(self):
        s = self.build()
        self.assertIn("files_scanned", s["run"])
        self.assertIn("malformed_lines", s["run"])
        self.assertEqual(s["run"]["accounts_skipped"], [])


if __name__ == "__main__":
    unittest.main()
