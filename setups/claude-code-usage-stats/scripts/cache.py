"""Incremental parse cache keyed on (abspath, mtime_ns, size).

Transcripts are append-only, so any new content changes `size` and the key
self-invalidates. A rollup-schema bump discards the whole cache.
"""
import json
import os


class Cache:
    def __init__(self, path, rollup_schema, entries=None):
        self.path = path
        self.rollup_schema = rollup_schema
        self._entries = entries or {}
        self._next = {}
        self.hits = 0
        self.misses = 0

    @classmethod
    def load(cls, path, rollup_schema):
        entries = {}
        try:
            with open(path) as fh:
                blob = json.load(fh)
            if blob.get("rollup_schema") == rollup_schema:
                entries = blob.get("entries") or {}
        except (OSError, ValueError, AttributeError):
            entries = {}
        return cls(path, rollup_schema, entries)

    @staticmethod
    def _key(file_path):
        st = os.stat(file_path)
        return f"{os.path.abspath(file_path)}|{st.st_mtime_ns}|{st.st_size}"

    def get(self, file_path):
        try:
            key = self._key(file_path)
        except OSError:
            self.misses += 1
            return None
        hit = self._entries.get(key)
        if hit is None:
            self.misses += 1
            return None
        self.hits += 1
        self._next[key] = hit
        return hit

    def put(self, file_path, rollup):
        try:
            self._next[self._key(file_path)] = rollup
        except OSError:
            pass

    def save(self):
        """Write only keys touched this run, so deleted files age out."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"rollup_schema": self.rollup_schema, "entries": self._next}, fh)
        os.replace(tmp, self.path)
