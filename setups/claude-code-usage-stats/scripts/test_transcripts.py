import json, tempfile, unittest
from pathlib import Path
import transcripts

TS1 = "2026-08-11T04:02:11.000Z"
TS2 = "2026-08-11T06:40:02.000Z"
TS3 = "2026-08-13T01:00:00.000Z"


def usage(i, o, cr, cw):
    """A usage block shaped like the real thing -- including the iterations[]
    array that repeats the same numbers and MUST NOT be double-counted."""
    return {
        "input_tokens": i, "output_tokens": o,
        "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
        "iterations": [{"input_tokens": i, "output_tokens": o,
                        "cache_read_input_tokens": cr,
                        "cache_creation_input_tokens": cw}],
    }


def assistant(mid, model="claude-sonnet-5", ts=TS1, u=None, tools=(), sidechain=False):
    content = [{"type": "tool_use", "name": t} for t in tools]
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1",
            "isSidechain": sidechain, "cwd": "/repo", "gitBranch": "main",
            "version": "2.1.220",
            "message": {"id": mid, "model": model, "content": content,
                        "usage": u or usage(2, 40, 1000, 10)}}


def write(path, rows):
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestTranscripts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "session.jsonl"

    def test_iterations_not_double_counted(self):
        write(self.p, [assistant("m1", u=usage(10, 20, 30, 40))])
        r = transcripts.rollup_file(self.p)
        tok = list(r["by_day_model"].values())[0]
        self.assertEqual((tok["in"], tok["out"], tok["cr"], tok["cw"]), (10, 20, 30, 40))

    def test_repeated_message_id_counted_once(self):
        write(self.p, [assistant("m1", u=usage(10, 20, 30, 40)),
                       assistant("m1", u=usage(10, 20, 30, 40)),
                       assistant("m2", u=usage(1, 2, 3, 4))])
        r = transcripts.rollup_file(self.p)
        tok = list(r["by_day_model"].values())[0]
        self.assertEqual(tok["in"], 11)   # 10 + 1, not 21
        self.assertEqual(tok["out"], 22)

    def test_tools_branches_versions_counted(self):
        write(self.p, [assistant("m1", tools=("Bash", "Read", "Bash"))])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["tools"], {"Bash": 2, "Read": 1})
        self.assertEqual(r["branches"], {"main": 1})
        self.assertEqual(r["versions"], {"2.1.220": 1})

    def test_sidechain_split_out(self):
        write(self.p, [assistant("m1"), assistant("m2", sidechain=True)])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["sidechain_turns"], 1)

    def test_session_span_and_resumed_flag(self):
        write(self.p, [assistant("m1", ts=TS1), assistant("m2", ts=TS3)])
        r = transcripts.rollup_file(self.p)
        s = r["sessions"]["s1"]
        self.assertEqual(s["first_ts"], "2026-08-11T04:02:11Z")
        self.assertEqual(s["last_ts"], "2026-08-13T01:00:00Z")
        self.assertTrue(s["resumed"])          # spans >1 calendar day
        self.assertNotIn("duration", s)

    def test_single_day_session_not_resumed(self):
        write(self.p, [assistant("m1", ts=TS1), assistant("m2", ts=TS2)])
        r = transcripts.rollup_file(self.p)
        self.assertFalse(r["sessions"]["s1"]["resumed"])

    def test_one_message_many_lines_counts_one_turn_but_all_tools(self):
        """Real shape: one assistant message spans several JSONL lines, one per
        content block, sharing message.id and repeating usage verbatim.
        turns must count MESSAGES; tools must count every distinct block."""
        u = usage(2, 246, 1000, 75505)
        write(self.p, [
            assistant("mSame", u=u, tools=()),            # thinking-only line
            assistant("mSame", u=u, tools=()),            # text-only line
            assistant("mSame", u=u, tools=("Skill",)),    # tool_use line
        ])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["turns"], 1, "turns must be message-level, not line-level")
        self.assertEqual(r["tools"], {"Skill": 1}, "tool blocks must not be deduped away")
        tok = list(r["by_day_model"].values())[0]
        self.assertEqual(tok["in"], 2, "usage must be counted once per message")
        self.assertEqual(tok["turns"], r["turns"], "turns semantics must agree everywhere")
        self.assertEqual(r["branches"], {"main": 1}, "branch counts are message-level too")


    def test_malformed_lines_counted_not_fatal(self):
        self.p.write_text(json.dumps(assistant("m1")) + "\n{ nope\n")
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["malformed"], 1)
        self.assertEqual(r["turns"], 1)

    def test_missing_usage_is_safe(self):
        write(self.p, [{"type": "user", "timestamp": TS1, "sessionId": "s1",
                        "message": {"content": "hi"}}])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["user_turns"], 1)
        self.assertEqual(r["by_day_model"], {})


if __name__ == "__main__":
    unittest.main()
