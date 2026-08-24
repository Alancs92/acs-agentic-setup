#!/usr/bin/env python3
"""Tests for redact.py -- strip secrets before anything reaches OneDrive.

Two properties are load-bearing and pull in opposite directions:

  SECURITY   No real token may survive into the output. These blobs land in
             corporate OneDrive; a leaked token is a real incident.
  DETERMINISM Identical input must produce byte-identical output, forever.
             Redaction runs before hashing, so any nondeterminism here (key
             reordering, a random placeholder, a timestamp) fabricates a
             snapshot on every cycle.
"""
from __future__ import annotations

import hashlib
import json
import unittest

import redact

CONFIG = {
    "mode": "redact",
    "key_patterns": ["token", "secret", "password", "apikey", "api_key",
                     "credential", "auth"],
    "value_patterns": ["^sk-ant-", "^ghp_", "^gho_", "^github_pat_",
                       "^xox[baprs]-", "^Bearer\\s+"],
}

# Realistic shapes, none of them real credentials.
ANTHROPIC_KEY = "sk-ant-api03-" + "A" * 40
GITHUB_PAT = "ghp_" + "B" * 36
SLACK_BOT = "xoxb-123456789012-987654321098-" + "C" * 24
BEARER = "Bearer abc123"


def marker(value: str) -> str:
    return "«REDACTED:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] + "»"


_DEFAULT = object()  # sentinel: an EMPTY config dict is a meaningful input


class _Helper(unittest.TestCase):
    def run_redact(self, obj, filename="settings.json", config=_DEFAULT):
        data = json.dumps(obj, indent=2).encode("utf-8")
        cfg = CONFIG if config is _DEFAULT else config
        out, count = redact.redact_bytes(data, filename, cfg)
        return out, count

    def roundtrip(self, obj, filename="settings.json", config=_DEFAULT):
        out, count = self.run_redact(obj, filename, config)
        return json.loads(out.decode("utf-8")), count, out

    def assertNoSecrets(self, blob: bytes, *secrets: str):
        text = blob.decode("utf-8")
        for s in secrets:
            self.assertNotIn(s, text, f"secret leaked into output: {s[:8]}...")


class TestNonJsonPassthrough(_Helper):
    def test_markdown_passes_through_byte_identical(self):
        data = ("# Skill\n\nexport ANTHROPIC_API_KEY=" + ANTHROPIC_KEY + "\n").encode("utf-8")
        out, count = redact.redact_bytes(data, "SKILL.md", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_shell_script_passes_through(self):
        data = b"#!/bin/sh\ntoken=sk-ant-api03-zzz\n"
        out, count = redact.redact_bytes(data, "run.sh", CONFIG)
        self.assertIs(out, data)
        self.assertEqual(count, 0)

    def test_binary_file_passes_through(self):
        data = bytes(range(256))
        out, count = redact.redact_bytes(data, "logo.png", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_jsonl_is_not_treated_as_json(self):
        data = b'{"token":"a"}\n{"token":"b"}\n'
        out, count = redact.redact_bytes(data, "catalog.jsonl", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_json_detection_is_case_insensitive_on_extension(self):
        data = json.dumps({"token": ANTHROPIC_KEY}).encode("utf-8")
        out, count = redact.redact_bytes(data, "Settings.JSON", CONFIG)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)

    def test_full_path_as_filename_still_detected(self):
        data = json.dumps({"token": ANTHROPIC_KEY}).encode("utf-8")
        out, count = redact.redact_bytes(data, "personal/settings.local.json", CONFIG)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)


class TestMalformedInput(_Helper):
    def test_malformed_json_returns_unchanged_without_raising(self):
        data = b'{"token": "sk-ant-api03-broken",,,}'
        out, count = redact.redact_bytes(data, "settings.json", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_empty_file_returns_unchanged(self):
        out, count = redact.redact_bytes(b"", "settings.json", CONFIG)
        self.assertEqual(out, b"")
        self.assertEqual(count, 0)

    def test_truncated_json_returns_unchanged(self):
        data = b'{"env": {"ANTHROPIC_AUTH_TOKEN": "sk-ant-'
        out, count = redact.redact_bytes(data, "settings.json", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_invalid_utf8_returns_unchanged(self):
        data = b'{"token": "\xff\xfe"}'
        out, count = redact.redact_bytes(data, "settings.json", CONFIG)
        self.assertEqual(out, data)
        self.assertEqual(count, 0)

    def test_json_scalar_at_top_level_is_handled(self):
        out, count = redact.redact_bytes(b'"' + ANTHROPIC_KEY.encode() + b'"',
                                         "settings.json", CONFIG)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)


class TestKeyMatching(_Helper):
    def test_key_substring_match_is_case_insensitive(self):
        for key in ("token", "TOKEN", "authToken", "AUTH_TOKEN",
                    "apiKey", "API_KEY", "myPassword", "aws_secret_x",
                    "credentialFile", "OAUTH"):
            with self.subTest(key=key):
                obj, count, out = self.roundtrip({key: "plainvalue"})
                self.assertEqual(count, 1, f"{key} should have matched")
                self.assertEqual(obj[key], marker("plainvalue"))

    def test_non_matching_key_with_benign_value_is_untouched(self):
        obj, count, out = self.roundtrip({"model": "claude-opus-5", "theme": "dark"})
        self.assertEqual(count, 0)
        self.assertEqual(obj, {"model": "claude-opus-5", "theme": "dark"})

    def test_only_string_values_are_redacted(self):
        payload = {
            "tokenCount": 42,
            "tokenEnabled": True,
            "tokenDisabled": False,
            "tokenValue": None,
            "tokenRatio": 1.5,
        }
        obj, count, out = self.roundtrip(payload)
        self.assertEqual(count, 0)
        self.assertEqual(obj, payload)

    def test_keys_themselves_are_never_rewritten(self):
        obj, count, out = self.roundtrip({"secretKey": "value"})
        self.assertIn("secretKey", obj)

    def test_empty_string_value_under_secret_key_is_still_redacted(self):
        obj, count, out = self.roundtrip({"token": ""})
        self.assertEqual(count, 1)
        self.assertEqual(obj["token"], marker(""))


class TestValueMatching(_Helper):
    def test_anthropic_key_under_innocuous_key(self):
        obj, count, out = self.roundtrip({"note": ANTHROPIC_KEY})
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)
        self.assertEqual(obj["note"], marker(ANTHROPIC_KEY))

    def test_github_pat_under_innocuous_key(self):
        obj, count, out = self.roundtrip({"note": GITHUB_PAT})
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, GITHUB_PAT)

    def test_slack_bot_token_under_innocuous_key(self):
        obj, count, out = self.roundtrip({"webhook": SLACK_BOT})
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, SLACK_BOT)

    def test_bearer_header_under_innocuous_key(self):
        obj, count, out = self.roundtrip({"Authorization_hdr": BEARER})
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, BEARER, "abc123")

    def test_value_patterns_are_anchored_as_written(self):
        """'^sk-ant-' must not fire on a value that merely mentions it later."""
        obj, count, out = self.roundtrip({"doc": "keys look like sk-ant-api03-..."})
        self.assertEqual(count, 0)

    def test_bad_regex_in_config_does_not_crash(self):
        cfg = dict(CONFIG, value_patterns=["^sk-ant-", "([unclosed"])
        obj, count, out = self.roundtrip({"note": ANTHROPIC_KEY}, config=cfg)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)


class TestNesting(_Helper):
    def test_nested_object_env_auth_token(self):
        payload = {"env": {"ANTHROPIC_AUTH_TOKEN": ANTHROPIC_KEY,
                           "EDITOR": "vim"}}
        obj, count, out = self.roundtrip(payload)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)
        self.assertEqual(obj["env"]["EDITOR"], "vim")
        self.assertEqual(obj["env"]["ANTHROPIC_AUTH_TOKEN"], marker(ANTHROPIC_KEY))

    def test_deeply_nested_mixed_containers(self):
        payload = {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "server", "--token", GITHUB_PAT],
                    "env": {"GITHUB_TOKEN": GITHUB_PAT},
                    "headers": [{"Authorization": BEARER}],
                }
            },
            "permissions": {"allow": ["Bash(ls:*)", "Read"]},
        }
        obj, count, out = self.roundtrip(payload)
        self.assertNoSecrets(out, GITHUB_PAT, BEARER, "abc123")
        self.assertEqual(count, 3)
        srv = obj["mcpServers"]["github"]
        self.assertEqual(srv["command"], "npx")
        self.assertEqual(srv["args"][:3], ["-y", "server", "--token"])
        self.assertEqual(srv["args"][3], marker(GITHUB_PAT))
        self.assertEqual(obj["permissions"]["allow"], ["Bash(ls:*)", "Read"])

    def test_array_of_strings_under_secret_key_is_redacted(self):
        payload = {"tokens": ["plain-one", "plain-two"]}
        obj, count, out = self.roundtrip(payload)
        self.assertEqual(count, 2)
        self.assertEqual(obj["tokens"], [marker("plain-one"), marker("plain-two")])

    def test_top_level_array(self):
        payload = [{"token": "abc"}, {"model": "opus"}, ANTHROPIC_KEY]
        obj, count, out = self.roundtrip(payload)
        self.assertEqual(count, 2)
        self.assertNoSecrets(out, ANTHROPIC_KEY)
        self.assertEqual(obj[1], {"model": "opus"})

    def test_secret_key_holding_object_recurses_rather_than_stringifying(self):
        payload = {"auth": {"type": "oauth", "access_token": GITHUB_PAT}}
        obj, count, out = self.roundtrip(payload)
        self.assertNoSecrets(out, GITHUB_PAT)
        self.assertEqual(obj["auth"]["type"], "oauth",
                         "non-secret siblings under a secret-named object are kept")
        self.assertEqual(count, 1)

    def test_structure_is_otherwise_preserved(self):
        payload = {"a": {"b": {"c": [1, 2, {"d": None, "token": "x"}]}}}
        obj, count, out = self.roundtrip(payload)
        self.assertEqual(obj["a"]["b"]["c"][:2], [1, 2])
        self.assertIsNone(obj["a"]["b"]["c"][2]["d"])


class TestPlaceholder(_Helper):
    def test_placeholder_format_is_exact(self):
        obj, count, out = self.roundtrip({"token": ANTHROPIC_KEY})
        digest = hashlib.sha256(ANTHROPIC_KEY.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(obj["token"], "«REDACTED:" + digest + "»")
        self.assertEqual(len(digest), 12)
        int(digest, 16)

    def test_placeholder_hashes_the_original_value_not_the_key(self):
        a, _, _ = self.roundtrip({"token": "same-value"})
        b, _, _ = self.roundtrip({"password": "same-value"})
        self.assertEqual(a["token"], b["password"])

    def test_different_secrets_get_different_placeholders(self):
        obj, count, out = self.roundtrip({"tokenA": ANTHROPIC_KEY, "tokenB": GITHUB_PAT})
        self.assertNotEqual(obj["tokenA"], obj["tokenB"])

    def test_placeholder_survives_a_second_pass_stably(self):
        """Re-redacting an already-redacted document must be a no-op."""
        first, _, out1 = self.roundtrip({"token": ANTHROPIC_KEY})
        out2, count2 = redact.redact_bytes(out1, "settings.json", CONFIG)
        second = json.loads(out2.decode("utf-8"))
        self.assertNotEqual(second["token"], first["token"],
                            "placeholder is itself under a secret key, so it is "
                            "re-marked -- but must do so deterministically")
        out3, _ = redact.redact_bytes(out1, "settings.json", CONFIG)
        self.assertEqual(out2, out3)


class TestDeterminism(_Helper):
    def test_identical_input_yields_byte_identical_output(self):
        payload = {"env": {"ANTHROPIC_AUTH_TOKEN": ANTHROPIC_KEY},
                   "mcpServers": {"x": {"args": [GITHUB_PAT, SLACK_BOT]}},
                   "model": "opus"}
        data = json.dumps(payload, indent=2).encode("utf-8")
        outs = {redact.redact_bytes(data, "settings.json", CONFIG)[0] for _ in range(20)}
        self.assertEqual(len(outs), 1)

    def test_key_order_is_preserved_not_sorted(self):
        data = b'{"zebra": 1, "alpha": 2, "token": "x"}'
        out, count = redact.redact_bytes(data, "settings.json", CONFIG)
        text = out.decode("utf-8")
        self.assertLess(text.index("zebra"), text.index("alpha"),
                        "reordering keys would fabricate a snapshot")

    def test_clean_json_passes_through_byte_identical(self):
        """No secrets means no reformatting -- otherwise every manifest churns."""
        data = b'{"model":"opus",\n   "theme": "dark"}'
        out, count = redact.redact_bytes(data, "settings.json", CONFIG)
        self.assertEqual(count, 0)
        self.assertEqual(out, data)

    def test_output_is_valid_utf8_json(self):
        obj, count, out = self.roundtrip({"token": ANTHROPIC_KEY, "unicode": "café"})
        reparsed = json.loads(out.decode("utf-8"))
        self.assertEqual(reparsed["unicode"], "café")

    def test_changing_a_secret_changes_the_output(self):
        """Change detection must survive redaction."""
        _, _, out_a = self.roundtrip({"token": ANTHROPIC_KEY})
        _, _, out_b = self.roundtrip({"token": GITHUB_PAT})
        self.assertNotEqual(out_a, out_b)

    def test_changing_a_nonsecret_changes_the_output(self):
        _, _, out_a = self.roundtrip({"token": ANTHROPIC_KEY, "model": "opus"})
        _, _, out_b = self.roundtrip({"token": ANTHROPIC_KEY, "model": "sonnet"})
        self.assertNotEqual(out_a, out_b)


class TestConfigTolerance(_Helper):
    def test_missing_config_sections_default_to_no_patterns(self):
        obj, count, out = self.roundtrip({"token": ANTHROPIC_KEY}, config={})
        self.assertEqual(count, 0)

    def test_key_patterns_only(self):
        cfg = {"key_patterns": ["token"]}
        obj, count, out = self.roundtrip({"token": "a", "note": ANTHROPIC_KEY}, config=cfg)
        self.assertEqual(count, 1)

    def test_value_patterns_only(self):
        cfg = {"value_patterns": ["^sk-ant-"]}
        obj, count, out = self.roundtrip({"token": "a", "note": ANTHROPIC_KEY}, config=cfg)
        self.assertEqual(count, 1)
        self.assertNoSecrets(out, ANTHROPIC_KEY)

    def test_real_config_json_shape_is_accepted(self):
        import pathlib
        cfg_path = pathlib.Path(__file__).resolve().parent.parent / "config.json"
        real = json.loads(cfg_path.read_text())["secrets"]
        obj, count, out = self.roundtrip(
            {"env": {"ANTHROPIC_AUTH_TOKEN": ANTHROPIC_KEY},
             "note": SLACK_BOT}, config=real)
        self.assertEqual(count, 2)
        self.assertNoSecrets(out, ANTHROPIC_KEY, SLACK_BOT)


if __name__ == "__main__":
    unittest.main()
