# scripts/test_history.py
import json, tempfile, unittest
from pathlib import Path
import history

# 2026-08-11T04:02:11Z -- deliberately an hour that is a DIFFERENT DAY in
# Australia/Sydney (14:02 local), so a local-time bug shows up as a wrong bucket.
TS_A = 1786420931000
# 2026-08-11T23:30:00Z -- 09:30 next day in Sydney.
TS_B = 1786491000000


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def row(ts, display, project="~/repos/x", session="s1"):
    return {"display": display, "timestamp": ts, "project": project,
            "sessionId": session, "pastedContents": {}}


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_malformed_lines_counted_not_fatal(self):
        p = self.tmp / "history.jsonl"
        p.write_text(json.dumps(row(TS_A, "one")) + "\n{ broken\n" + json.dumps(row(TS_B, "two")) + "\n\n")
        entries, malformed = history.read_history(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(malformed, 1)  # blank lines are skipped, not counted

    def test_slash_and_chars_extracted(self):
        p = self.tmp / "history.jsonl"
        write(p, [row(TS_A, "/code-review please"), row(TS_B, "plain prompt")])
        entries, _ = history.read_history(p)
        self.assertEqual(entries[0]["slash"], "/code-review")
        self.assertIsNone(entries[1]["slash"])
        self.assertEqual(entries[0]["chars"], len("/code-review please"))

    def test_three_figures_and_shared_identity(self):
        shared = [row(TS_A, "shared-one"), row(TS_B, "shared-two")]
        a = shared + [row(TS_A, "a-only")]
        b = shared + [row(TS_A, "b-only-1"), row(TS_B, "b-only-2")]
        pa, _ = history.read_history_rows(a)
        pb, _ = history.read_history_rows(b)

        fig = history.dedup_figures({"a": pa, "b": pb})

        self.assertEqual(fig["per_account"]["a"], {"total": 3, "exclusive": 1, "shared": 2})
        self.assertEqual(fig["per_account"]["b"], {"total": 4, "exclusive": 2, "shared": 2})
        self.assertEqual(fig["union"], 5)          # 2 shared + 1 + 2
        self.assertEqual(fig["sum_of_totals"], 7)
        for name, f in fig["per_account"].items():
            self.assertEqual(f["shared"], f["total"] - f["exclusive"], name)
        self.assertLessEqual(fig["union"], fig["sum_of_totals"])

    def test_figures_invariant_under_account_order(self):
        pa, _ = history.read_history_rows([row(TS_A, "s"), row(TS_A, "a1")])
        pb, _ = history.read_history_rows([row(TS_A, "s"), row(TS_B, "b1")])
        one = history.dedup_figures({"a": pa, "b": pb})
        two = history.dedup_figures({"b": pb, "a": pa})
        self.assertEqual(one, two)

    def test_buckets_are_utc_and_lossless(self):
        pa, _ = history.read_history_rows([
            row(TS_A, "p1", project="~/repos/x"),
            row(TS_A, "p2", project="~/repos/x"),
            row(TS_B, "p3", project="~/repos/y"),
        ])
        fig = history.dedup_figures({"a": pa})
        rows = history.bucket_prompts({"a": pa}, fig["exclusive_keys"])

        by_key = {(r["d"], r["h"], r["p"]): r["n"] for r in rows}
        # TS_A is 04:00 UTC on 2026-08-11 -- NOT 14:00 on the same local day
        self.assertEqual(by_key[("2026-08-11", 4, "~/repos/x")], 2)
        # TS_B is 23:00 UTC on 2026-08-11, which is the NEXT day in Sydney
        self.assertEqual(by_key[("2026-08-11", 23, "~/repos/y")], 1)
        self.assertEqual(sum(r["n"] for r in rows), 3)
        self.assertLessEqual(len(rows), 3)

    def test_char_stats(self):
        entries = [{"chars": c} for c in [10, 20, 30, 40, 100]]
        st = history.char_stats(entries)
        self.assertEqual(st["max"], 100)
        self.assertEqual(st["median"], 30)
        self.assertEqual(history.char_stats([]), {})


if __name__ == "__main__":
    unittest.main()
