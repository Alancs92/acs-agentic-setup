# scripts/test_discovery.py
import tempfile, unittest
from pathlib import Path
import discovery


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.canonical = self.tmp / "canonical"

    def _mk(self, path, history=True, projects=True):
        path.mkdir(parents=True, exist_ok=True)
        if history:
            (path / "history.jsonl").write_text("")
        if projects:
            (path / "projects").mkdir(exist_ok=True)

    def test_finds_accounts_and_excludes_bin(self):
        self._mk(self.store / "harrison")
        self._mk(self.store / "personal")
        # bin/ lives inside the store but is not an account
        (self.store / "bin").mkdir(parents=True)
        (self.store / "bin" / "some-script.sh").write_text("#!/bin/sh\n")
        self._mk(self.canonical)

        accounts = discovery.discover_accounts(self.store, self.canonical)
        names = [a.name for a in accounts]

        self.assertEqual(names, ["harrison", "personal", "canonical"])
        self.assertTrue(accounts[-1].is_canonical)
        self.assertFalse(accounts[0].is_canonical)

    def test_history_only_account_is_valid(self):
        self._mk(self.store / "personal2", history=True, projects=False)
        accounts = discovery.discover_accounts(self.store, self.canonical)
        self.assertEqual([a.name for a in accounts], ["personal2"])
        self.assertIsNone(accounts[0].projects_dir)
        self.assertIsNotNone(accounts[0].history_path)

    def test_projects_only_account_is_valid(self):
        self._mk(self.store / "fresh", history=False, projects=True)
        accounts = discovery.discover_accounts(self.store, self.canonical)
        self.assertEqual([a.name for a in accounts], ["fresh"])
        self.assertIsNone(accounts[0].history_path)

    def test_missing_store_returns_empty(self):
        self.assertEqual(discovery.discover_accounts(self.tmp / "nope", self.tmp / "nope2"), [])


if __name__ == "__main__":
    unittest.main()
