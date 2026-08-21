# scripts/render.py
"""Inline a stats dict into the dashboard template."""
import json

PLACEHOLDER = "<!--STATS_JSON-->"


def render(template_text, stats):
    if PLACEHOLDER not in template_text:
        raise ValueError(f"template is missing the {PLACEHOLDER} marker")
    blob = json.dumps(stats, indent=1, sort_keys=False)
    # A project path could contain "</script>" and break out of the block.
    blob = blob.replace("</", "<\\/")
    block = f'<script id="stats-data" type="application/json">\n{blob}\n</script>'
    return template_text.replace(PLACEHOLDER, block)
