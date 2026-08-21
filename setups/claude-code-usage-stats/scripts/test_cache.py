import json, tempfile, time, unittest
from pathlib import Path
import cache


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache_path = self.tmp / "cache.json"
        self.data = self.tmp / "f.jsonl"
        self.data.write_text("one\n")

    def test_miss_then_hit(self):
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.data))
        c.put(self.data, {"turns": 5})
        c.save()

        c2 = cache.Cache.load(self.cache_path, 1)
        self.assertEqual(c2.get(self.data), {"turns": 5})
        self.assertEqual(c2.hits, 1)

    def test_size_change_invalidates(self):
        c = cache.Cache.load(self.cache_path, 1)
        c.put(self.data, {"turns": 5})
        c.save()

        self.data.write_text("one\ntwo\n")        # append -> size changes
        c2 = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c2.get(self.data))
        self.assertEqual(c2.misses, 1)

    def test_schema_bump_discards_everything(self):
        c = cache.Cache.load(self.cache_path, 1)
        c.put(self.data, {"turns": 5})
        c.save()

        c2 = cache.Cache.load(self.cache_path, 2)
        self.assertIsNone(c2.get(self.data))

    def test_corrupt_cache_file_is_survivable(self):
        self.cache_path.write_text("{ not json")
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.data))
        c.put(self.data, {"turns": 1})
        c.save()
        self.assertEqual(cache.Cache.load(self.cache_path, 1).get(self.data), {"turns": 1})

    def test_missing_file_is_a_miss_not_a_crash(self):
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.tmp / "does-not-exist.jsonl"))


if __name__ == "__main__":
    unittest.main()
