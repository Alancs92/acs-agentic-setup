#!/usr/bin/env python3
"""Tests for the herdr setup: acs.attention.

Nothing touches the live machine. Stdlib unittest.

Run: python3 setups/herdr/scripts/test_herdr_setup.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.dirname(HERE)
ATTENTION = os.path.join(SETUP, "config", "herdr", "plugins", "attention", "attention.py")
def load_attention(state_dir):
    os.environ["HERDR_PLUGIN_STATE_DIR"] = state_dir
    os.environ["HERDR_SOCKET_PATH"] = "/x/sessions/foo/herdr.sock"
    spec = importlib.util.spec_from_file_location("attention_under_test", ATTENTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AttentionMarkPruning(unittest.TestCase):
    DAY = 24 * 3600

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="herdr-attn-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.mod = load_attention(self.tmp)
        self.applied = []
        self.mod.apply = lambda pane_id, mark: self.applied.append((pane_id, mark))
        self.mod.panes = lambda: [{"pane_id": "w1:p1", "agent_session": {"value": "LIVE"}}]
        self.now = 1_000 * self.DAY
        self.mod.time.time = lambda: self.now
        self.addCleanup(setattr, self.mod.time, "time", __import__("time").time)

    def seed(self, marks):
        self.mod.save_state({"foo": marks})

    def marks(self):
        return self.mod.load_state()["foo"]

    def test_matched_marks_are_applied(self):
        self.seed({"session:LIVE": {"mark": "later", "pane_id": "w1:p1"}})
        self.assertEqual(self.mod.restore_once(), 0)
        self.assertEqual(self.applied, [("w1:p1", "later")])

    def test_old_unmatched_marks_are_pruned_on_the_final_pass(self):
        old = self.now - 30 * self.DAY
        self.seed({"session:GONE": {"mark": "later", "pane_id": "w9:p9", "last_seen": old}})
        self.assertEqual(self.mod.restore_once(prune=True), 0)
        self.assertNotIn("session:GONE", self.marks())

    def test_old_unmatched_marks_survive_an_early_pass(self):
        """Agents report session ids a little after start; never prune before they have."""
        old = self.now - 30 * self.DAY
        self.seed({"session:GONE": {"mark": "later", "pane_id": "w9:p9", "last_seen": old}})
        self.assertEqual(self.mod.restore_once(), 1)
        self.assertIn("session:GONE", self.marks())

    def test_recent_unmatched_marks_survive_the_final_pass(self):
        recent = self.now - 2 * self.DAY
        self.seed({"session:AWAY": {"mark": "review", "pane_id": "w9:p9", "last_seen": recent}})
        self.mod.restore_once(prune=True)
        self.assertIn("session:AWAY", self.marks())

    def test_legacy_marks_without_last_seen_start_the_clock(self):
        self.seed({"session:OLD": {"mark": "later", "pane_id": "w9:p9"}})
        self.mod.restore_once(prune=True)
        self.assertEqual(self.marks()["session:OLD"]["last_seen"], self.now)

    def test_matching_refreshes_last_seen(self):
        old = self.now - 10 * self.DAY
        self.seed({"session:LIVE": {"mark": "later", "pane_id": "w1:p1", "last_seen": old}})
        self.mod.restore_once()
        self.assertEqual(self.marks()["session:LIVE"]["last_seen"], self.now)

    def test_set_mark_records_last_seen(self):
        pane = {"pane_id": "w1:p1", "agent_session": {"value": "LIVE"}}
        self.mod.set_mark(pane, "waiting")
        self.assertEqual(self.marks()["session:LIVE"]["last_seen"], self.now)


if __name__ == "__main__":
    unittest.main()
