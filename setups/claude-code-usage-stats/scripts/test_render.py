# scripts/test_render.py
import json, unittest
import render


class TestRender(unittest.TestCase):
    def test_injects_json_at_placeholder(self):
        out = render.render(f"<body>{render.PLACEHOLDER}</body>", {"schema_version": 1})
        self.assertIn('<script id="stats-data" type="application/json">', out)
        self.assertIn('"schema_version": 1', out)
        self.assertNotIn(render.PLACEHOLDER, out)

    def test_escapes_closing_script_tag(self):
        """A project path containing </script> must not break out of the block."""
        out = render.render(render.PLACEHOLDER, {"p": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script><script>alert(1)", out)
        self.assertIn("<\\/script>", out)

    def test_missing_placeholder_is_an_error(self):
        with self.assertRaises(ValueError):
            render.render("<body>no marker</body>", {})


if __name__ == "__main__":
    unittest.main()
