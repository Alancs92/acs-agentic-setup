#!/usr/bin/env python3
"""Strip secrets out of JSON before a single byte reaches the blob store.

These archives land in corporate OneDrive. `settings.json` legitimately carries
API keys, OAuth tokens and MCP server credentials, so this module runs BEFORE
store.py writes anything -- there is no second chance once a blob is synced.

LEGEND
  key match     The property NAME contains one of secrets_config['key_patterns']
                as a case-insensitive substring. Catches ANTHROPIC_AUTH_TOKEN,
                apiKey, myPassword without needing to know the value's shape.
  value match   The string VALUE matches one of the regexes in
                secrets_config['value_patterns']. Catches a token hiding under
                an innocuous name like "note" or a positional CLI arg.
  marker        The replacement, «REDACTED:<12 hex>», where the hex is the first
                12 characters of sha256(original value).

WHY A HASHED MARKER RATHER THAN A CONSTANT

  Change detection runs on the redacted bytes. If every secret collapsed to a
  fixed "«REDACTED»", rotating a token would be invisible to the backup and the
  new value would never be snapshotted. If instead the marker carried the value,
  the secret would be in OneDrive. Hashing gives change detection without
  disclosure: a rotated key produces a different marker, and the marker is not
  reversible.

TWO PROPERTIES THAT PULL AGAINST EACH OTHER

  SECURITY     no real secret may survive into the output.
  DETERMINISM  identical input must give byte-identical output, always. This
               module sits upstream of the content hash, so any instability here
               -- reordered keys, a random placeholder, a timestamp -- would
               fabricate a snapshot on every scheduled run.

  Both are satisfied by: preserve JSON key order (json.loads/dumps round-trips
  insertion order), never touch a document with zero redactions, and derive the
  marker purely from the value.

Inputs:  raw file bytes, the file's name, and config.json's `secrets` block
Outputs: (possibly-redacted bytes, number of values redacted)
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, List, Optional, Pattern, Tuple

MARKER_PREFIX = "«REDACTED:"   # «
MARKER_SUFFIX = "»"            # »
MARKER_HASH_LEN = 12

# Only real JSON is parsed. .jsonl is deliberately NOT included: it is a stream
# of documents, not a document, and json.loads would reject it anyway.
_JSON_SUFFIX = ".json"


def _marker(value: str) -> str:
    """«REDACTED:<first 12 hex of sha256(value)>» -- see module docstring."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return MARKER_PREFIX + digest[:MARKER_HASH_LEN] + MARKER_SUFFIX


def _compile(patterns: Any) -> List[Pattern]:
    """Compile value patterns, skipping any that are malformed.

    A typo in config.json must not take the whole backup down; the other
    patterns still apply and the key-name rules are unaffected.
    """
    compiled = []
    for pattern in patterns or ():
        if not isinstance(pattern, str):
            continue
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    return compiled


def _key_patterns(secrets_config: Any) -> List[str]:
    raw = (secrets_config or {}).get("key_patterns") or ()
    return [p.lower() for p in raw if isinstance(p, str)]


def _is_secret_key(key: str, key_patterns: List[str]) -> bool:
    """Case-insensitive SUBSTRING match, so "token" catches AUTH_TOKEN,
    authToken and refresh_token_v2 alike."""
    lowered = key.lower()
    return any(pattern in lowered for pattern in key_patterns)


def _is_secret_value(value: str, value_patterns: List[Pattern]) -> bool:
    """Regex search, with the patterns anchored as config.json writes them
    (`^sk-ant-` etc.), so prose mentioning a key prefix is not redacted."""
    return any(pattern.search(value) for pattern in value_patterns)


class _Walker:
    """Recursive redaction over the parsed document.

    `key_hit` carries "the enclosing property name matched" DOWN THROUGH ARRAYS
    but NOT through nested objects. An array is a multi-valued form of the same
    property -- {"tokens": ["a", "b"]} means both are tokens -- whereas a nested
    object introduces its own names, and those names are the better signal.
    Propagating into objects would redact {"auth": {"type": "oauth"}}'s harmless
    "oauth" while adding no security: the real secret inside is caught by its
    own key or by its value pattern.
    """

    def __init__(self, key_patterns: List[str], value_patterns: List[Pattern]):
        self._key_patterns = key_patterns
        self._value_patterns = value_patterns
        self.count = 0

    def walk(self, node: Any, key_hit: bool = False) -> Any:
        if isinstance(node, dict):
            # dict comprehension preserves insertion order, so key order in the
            # re-serialised document matches the source exactly.
            return {
                key: self.walk(
                    value,
                    key_hit=_is_secret_key(key, self._key_patterns)
                    if isinstance(key, str)
                    else False,
                )
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [self.walk(item, key_hit=key_hit) for item in node]
        if isinstance(node, str):
            if key_hit or _is_secret_value(node, self._value_patterns):
                self.count += 1
                return _marker(node)
            return node
        # Numbers, bools and null cannot carry a token and are left untouched --
        # rewriting them would corrupt settings on restore for no benefit.
        return node


def redact_bytes(
    data: bytes, filename: str, secrets_config: dict
) -> Tuple[bytes, int]:
    """Return (possibly-redacted bytes, count of redactions).

    Only JSON files are inspected; anything else passes through byte-identical
    with count 0. A value is redacted when its KEY matches
    secrets_config['key_patterns'] (case-insensitive substring) or its VALUE
    matches any of secrets_config['value_patterns'] (regex).

    Replacement is «REDACTED:<first 12 hex of sha256(value)>», which preserves
    change-detection without carrying the secret. Applies recursively through
    nested objects and arrays. Malformed JSON returns unchanged with count 0 --
    a settings file mid-edit must degrade to "backed up verbatim", never to an
    exception that aborts the run.

    When nothing is redacted the ORIGINAL bytes are returned untouched rather
    than a re-serialised equivalent. Reformatting a clean file would change its
    hash the first time this ran and churn every plugin manifest for nothing.
    """
    if not _looks_like_json(filename):
        return data, 0

    try:
        text = data.decode("utf-8")
        document = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return data, 0

    walker = _Walker(
        _key_patterns(secrets_config),
        _compile((secrets_config or {}).get("value_patterns")),
    )
    redacted = walker.walk(document)

    if walker.count == 0:
        return data, 0

    # indent=2 matches how Claude writes settings.json, and ensure_ascii=False
    # keeps the « » markers legible. Both are fixed, so the output is a pure
    # function of the input.
    rendered = json.dumps(redacted, indent=2, ensure_ascii=False)
    if text.endswith("\n"):
        rendered += "\n"
    return rendered.encode("utf-8"), walker.count


def looks_like_json(filename: Optional[str]) -> bool:
    """Decide by extension only. The filename may be a bare name or a
    unit-relative path such as 'personal/settings.local.json'.

    Public because hashing.py needs the same answer: it hashes the redacted form
    of a file, and only wants to materialise a file in full when redaction could
    possibly rewrite it. Both sides must agree on what counts as JSON or the
    hash would describe different bytes from the ones stored.
    """
    if not filename:
        return False
    return filename.lower().endswith(_JSON_SUFFIX)


_looks_like_json = looks_like_json  # internal alias, kept for call sites above
