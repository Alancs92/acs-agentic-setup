import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import fetch_engine


class TestHashing(unittest.TestCase):
    def test_sha256_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "blob"
            p.write_bytes(b"impeccable")
            self.assertEqual(
                fetch_engine.sha256_file(p),
                hashlib.sha256(b"impeccable").hexdigest(),
            )

    def test_verify_tarball_accepts_matching_sri(self):
        data = b"tarball-bytes"
        sri = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        self.assertTrue(fetch_engine.verify_tarball(data, sri))

    def test_verify_tarball_rejects_tampered_bytes(self):
        sri = "sha512-" + base64.b64encode(hashlib.sha512(b"good").digest()).decode()
        self.assertFalse(fetch_engine.verify_tarball(b"evil", sri))

    def test_verify_tarball_rejects_empty_integrity(self):
        self.assertFalse(fetch_engine.verify_tarball(b"anything", ""))

    def test_verify_tarball_rejects_unknown_algorithm(self):
        self.assertFalse(fetch_engine.verify_tarball(b"x", "md5-abc123"))


class TestLock(unittest.TestCase):
    def test_load_lock_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "engine.lock.json"
            p.write_text(json.dumps({
                "cli_version": "4.1.0",
                "engine_version": "0.1.5",
                "platform": "darwin-arm64",
                "binary_sha256": "ab" * 32,
                "route": "npm",
            }))
            lock = fetch_engine.load_lock(p)
            self.assertEqual(lock["engine_version"], "0.1.5")
            self.assertEqual(lock["cli_version"], "4.1.0")

    def test_verify_binary_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "impeccable"
            p.write_bytes(b"original")
            good = fetch_engine.sha256_file(p)
            self.assertTrue(fetch_engine.verify_binary(p, good))
            p.write_bytes(b"tampered")
            self.assertFalse(fetch_engine.verify_binary(p, good))

    def test_verify_binary_missing_file_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(fetch_engine.verify_binary(Path(d) / "nope", "ab" * 32))


class TestMainFailsClosed(unittest.TestCase):
    def test_missing_lock_exits_one_without_traceback(self):
        self.assertEqual(fetch_engine.main(["--lock", "/nonexistent.json"]), 1)

    def test_malformed_lock_exits_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "engine.lock.json"
            p.write_text("{not json")
            self.assertEqual(fetch_engine.main(["--lock", str(p)]), 1)


class TestPlatform(unittest.TestCase):
    def test_platform_target_shape(self):
        target = fetch_engine.platform_target()
        self.assertRegex(target, r"^(darwin|linux|windows)-(arm64|x64)$")


if __name__ == "__main__":
    unittest.main()
