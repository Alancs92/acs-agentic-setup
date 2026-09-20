import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import brand_lint

FIXTURES = Path(__file__).parent / "fixtures"
CASENOTE = brand_lint.load_profile("casenote")
SCRIPT = Path(__file__).with_name("brand_lint.py")


def run_cli(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class TestCore(unittest.TestCase):
    def test_clean_fixture_has_no_findings(self):
        path = FIXTURES / "casenote_clean.html"
        findings = brand_lint.scan_text(path.read_text(), str(path), CASENOTE)
        self.assertEqual(findings, [], f"clean fixture regressed: {findings}")

    def test_every_rule_declares_a_source(self):
        for rule_id, fn in brand_lint.RULES.items():
            self.assertTrue(getattr(fn, "source", ""),
                            f"{rule_id} has no brand-file source")

    def test_scan_paths_reports_unreadable(self):
        findings, unreadable = brand_lint.scan_paths([Path("/nonexistent.html")])
        self.assertEqual(findings, [])
        self.assertEqual(len(unreadable), 1)


class TestExitCodes(unittest.TestCase):
    def test_clean_exits_zero(self):
        code, _, _ = run_cli("--brand", "casenote", str(FIXTURES / "casenote_clean.html"))
        self.assertEqual(code, 0)

    def test_unreadable_exits_one(self):
        code, _, _ = run_cli("/nonexistent.html")
        self.assertEqual(code, 1)

    def test_unreadable_takes_precedence_over_findings(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, _, _ = run_cli("--brand", "casenote", str(bad), "/nonexistent.html")
            self.assertEqual(code, 1, "operational failure must beat findings")


class TestJsonShape(unittest.TestCase):
    def test_json_fields_match_upstream_shape(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, out, _ = run_cli("--brand", "casenote", "--json", str(bad))
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertGreaterEqual(len(payload), 1)
            self.assertEqual(
                set(payload[0]),
                {"rule", "severity", "file", "line", "snippet", "description", "source"},
            )


if __name__ == "__main__":
    unittest.main()


class TestTokenRules(unittest.TestCase):
    """Casenote-flavoured fixtures, linted under the Casenote profile."""

    def assert_flags(self, rule_id, fixture_name):
        path = FIXTURES / fixture_name
        findings = brand_lint.scan_text(path.read_text(), str(path), CASENOTE)
        ids = [f.rule for f in findings]
        self.assertIn(rule_id, ids, f"{fixture_name} did not trip {rule_id}: {ids}")

    def test_forbidden_token_names(self):
        self.assert_flags("forbidden-token-names", "forbidden_tokens_bad.html")

    def test_raw_hex_outside_root(self):
        self.assert_flags("raw-hex", "raw_hex_bad.html")

    def test_pure_black_on_white(self):
        self.assert_flags("pure-black-on-white", "pure_black_bad.html")

    def test_gradient_only_as_mark(self):
        self.assert_flags("gradient-only-as-mark", "gradient_only_bad.html")

    def test_status_as_series(self):
        self.assert_flags("status-as-series", "status_series_bad.html")

    def test_series_ceiling(self):
        self.assert_flags("series-ceiling", "series_ceiling_bad.html")

    def test_series_at_ceiling_is_legal(self):
        """Six categories is the ceiling; the seventh is the violation."""
        text = "<style>:root{--series-1:#0d9488;--series-6:#77712f;}</style>"
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html", CASENOTE)]
        self.assertNotIn("series-ceiling", ids)

    def test_gradient_use_is_legal(self):
        """--accent-bright is the gradient partner; that use is correct."""
        text = ("<style>.hairline{background:linear-gradient(90deg,"
                "var(--accent),var(--accent-bright));}</style>")
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html", CASENOTE)]
        self.assertNotIn("gradient-only-as-mark", ids)


class TestThemingRules(unittest.TestCase):
    def assert_flags(self, rule_id, fixture_name):
        path = FIXTURES / fixture_name
        findings = brand_lint.scan_text(path.read_text(), str(path))
        ids = [f.rule for f in findings]
        self.assertIn(rule_id, ids, f"{fixture_name} did not trip {rule_id}: {ids}")

    def test_light_dark_with_theme_stamp(self):
        self.assert_flags("light-dark-with-theme-stamp", "light_dark_bad.html")

    def test_theme_on_root(self):
        self.assert_flags("theme-on-root", "theme_on_root_bad.html")

    def test_light_dark_alone_is_not_flagged(self):
        """light-dark() is fine when nothing stamps data-theme."""
        text = "<style>:root{color:light-dark(#102420,#e9f0ec);}</style>"
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertNotIn("light-dark-with-theme-stamp", ids)

    def test_scoped_theme_container_is_not_flagged(self):
        """Scoping data-theme to a container is the correct pattern."""
        text = "<script>panel.dataset.theme='dark';</script>"
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertNotIn("theme-on-root", ids)

    def test_light_dark_with_js_dataset_stamp(self):
        """dataset.theme is the same stamp as data-theme, spelled from JS."""
        text = ("<style>:root{color:light-dark(#102420,#e9f0ec);}</style>"
                "<script>panel.dataset.theme='dark';</script>")
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertIn("light-dark-with-theme-stamp", ids)


class TestSvgRule(unittest.TestCase):
    def test_fixed_viewbox_without_min_width(self):
        path = FIXTURES / "svg_min_width_bad.html"
        ids = [f.rule for f in
               brand_lint.scan_text(path.read_text(), str(path))]
        self.assertIn("svg-no-min-width", ids)

    def test_min_width_present_is_clean(self):
        text = '<svg viewBox="0 0 600 300" style="width:100%;min-width:600px"></svg>'
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertNotIn("svg-no-min-width", ids)

    def test_svg_without_viewbox_is_ignored(self):
        text = '<svg style="width:100%"></svg>'
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertNotIn("svg-no-min-width", ids)

    def test_svg_width_attribute_form(self):
        text = '<svg viewBox="0 0 600 300" width="100%"></svg>'
        ids = [f.rule for f in brand_lint.scan_text(text, "x.html")]
        self.assertIn("svg-no-min-width", ids)


# ---------------------------------------------------------------------------
# Brand-agnosticism: the engine must carry no brand's values in its code.
# ---------------------------------------------------------------------------

# A second, entirely synthetic brand. Nothing here overlaps Casenote's tokens.
# If the engine were Casenote-shaped, these would not be detected.
ACME_PROFILE = {
    "name": "Acme",
    "series": {"prefix": "--chart-", "ceiling": 3},
    "gradient_only": ["--glow", "#ff00ff"],
    "status_tokens": ["--ok", "--bad"],
    "forbidden_tokens": [r"--legacy-\d+", "--old-brand"],
    "impeccable": {"ignoreRules": ["bounce-easing"]},
}


class TestBrandAgnostic(unittest.TestCase):
    def ids(self, text, profile):
        return [f.rule for f in brand_lint.scan_text(text, "x.html", profile)]

    def test_acme_forbidden_tokens_are_caught(self):
        text = "<style>:root{--legacy-3:#111111;}</style>"
        self.assertIn("forbidden-token-names", self.ids(text, ACME_PROFILE))

    def test_acme_gradient_only_token_as_mark_is_caught(self):
        text = "<style>.dot{fill:var(--glow);}</style>"
        self.assertIn("gradient-only-as-mark", self.ids(text, ACME_PROFILE))

    def test_acme_series_ceiling_is_three_not_six(self):
        text = "<style>:root{--chart-1:#111;--chart-4:#222;}</style>"
        self.assertIn("series-ceiling", self.ids(text, ACME_PROFILE))

    def test_acme_series_at_its_own_ceiling_is_legal(self):
        text = "<style>:root{--chart-1:#111;--chart-3:#222;}</style>"
        self.assertNotIn("series-ceiling", self.ids(text, ACME_PROFILE))

    def test_casenote_tokens_are_invisible_to_acme(self):
        """The engine holds no Casenote values; only the profile supplies them."""
        text = ("<style>:root{--swatch-1:#111;--series-9:#222;}"
                ".d{fill:var(--accent-bright);}</style>")
        found = self.ids(text, ACME_PROFILE)
        for rule_id in ("forbidden-token-names", "gradient-only-as-mark",
                        "series-ceiling"):
            self.assertNotIn(rule_id, found,
                             f"{rule_id} fired on Casenote tokens under the Acme profile")

    def test_universal_rules_fire_under_any_profile(self):
        text = '<svg viewBox="0 0 600 300" style="width:100%"></svg>'
        self.assertIn("svg-no-min-width", self.ids(text, ACME_PROFILE))

    def test_empty_profile_runs_universal_rules_only(self):
        text = ("<style>:root{--swatch-1:#111;}</style>"
                '<svg viewBox="0 0 1 1" style="width:100%"></svg>')
        found = self.ids(text, brand_lint.DEFAULT_PROFILE)
        self.assertIn("svg-no-min-width", found)
        self.assertNotIn("forbidden-token-names", found)


class TestProfileLoading(unittest.TestCase):
    def test_load_profile_by_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "acme.json"
            p.write_text(json.dumps(ACME_PROFILE))
            self.assertEqual(brand_lint.load_profile(p)["name"], "Acme")

    def test_load_profile_by_name_from_brands_dir(self):
        profile = brand_lint.load_profile("casenote")
        self.assertEqual(profile["name"], "Casenote")

    def test_missing_profile_raises(self):
        with self.assertRaises(FileNotFoundError):
            brand_lint.load_profile("no-such-brand")


class TestSourceStaleness(unittest.TestCase):
    def test_matching_hash_reports_no_drift(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "brand.md"
            src.write_text("# Brand\n")
            profile = {"source_file": str(src),
                       "source_sha256": brand_lint.sha256_text(src.read_text())}
            self.assertIsNone(brand_lint.check_source(profile))

    def test_changed_source_reports_drift(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "brand.md"
            src.write_text("# Brand\n")
            profile = {"source_file": str(src),
                       "source_sha256": brand_lint.sha256_text("# Brand\n")}
            src.write_text("# Brand\n\nNew rule added.\n")
            msg = brand_lint.check_source(profile)
            self.assertIsNotNone(msg)
            self.assertIn("changed", msg)

    def test_missing_source_reports_drift(self):
        profile = {"source_file": "/nonexistent.md", "source_sha256": "ab" * 32}
        self.assertIsNotNone(brand_lint.check_source(profile))

    def test_profile_without_hash_is_not_checked(self):
        self.assertIsNone(brand_lint.check_source({"name": "Acme"}))


class TestEmitConfig(unittest.TestCase):
    def test_emits_profile_ignore_rules(self):
        cfg = brand_lint.impeccable_config(ACME_PROFILE)
        self.assertEqual(cfg["detector"]["ignoreRules"], ["bounce-easing"])

    def test_casenote_profile_emits_overused_font(self):
        cfg = brand_lint.impeccable_config(brand_lint.load_profile("casenote"))
        self.assertIn("overused-font", cfg["detector"]["ignoreRules"])


class TestRawHexFalsePositives(unittest.TestCase):
    """raw-hex must not fire on things that merely look like a hex colour.

    A design linter that cries wolf gets ignored, so the legal directions are
    pinned as tightly as the violations.
    """

    def ids(self, text):
        return [f.rule for f in brand_lint.scan_text(text, "x.html", CASENOTE)]

    def test_html_numeric_entities_are_not_hex(self):
        """&#8321; is a subscript digit, not #8321. Found in the real
        usage-stats dashboard's log-scale axis labels."""
        text = "<p>10&#8321; and 10&#8320; on a log axis</p>"
        self.assertNotIn("raw-hex", self.ids(text))

    def test_url_fragment_is_not_hex(self):
        text = '<a href="#abc123">jump</a>'
        self.assertNotIn("raw-hex", self.ids(text))

    def test_real_hex_outside_root_still_fires(self):
        text = "<style>.card{color:#102420;}</style>"
        self.assertIn("raw-hex", self.ids(text))

    def test_real_hex_inside_root_still_passes(self):
        text = "<style>:root{--ink:#102420;}</style>"
        self.assertNotIn("raw-hex", self.ids(text))
