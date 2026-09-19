#!/usr/bin/env python3
"""Lint HTML/CSS against the mechanically-testable half of Casenote's denylist.

Source of truth for every token value is brands/alan-personal.md in the
brand-guidelines skill. Each rule records the brand-file section it encodes in
its `source` field, so a brand change that invalidates a rule stays findable.

Exit codes match Impeccable's so both tools compose in one CI step:
  0  no findings
  1  a target could not be scanned (takes precedence)
  2  findings
"""
import argparse
import json
import re
import sys
from collections import namedtuple
from pathlib import Path

Finding = namedtuple("Finding", "rule severity file line snippet description source")

SCANNED_SUFFIXES = {".html", ".htm", ".css", ".svg"}
RULES = {}


def rule(rule_id, severity, description, source):
    """Register a rule function. Signature: (text, path) -> list[Finding]."""
    def decorate(fn):
        fn.rule_id = rule_id
        fn.severity = severity
        fn.description = description
        fn.source = source
        RULES[rule_id] = fn
        return fn
    return decorate


def line_of(text, index):
    """1-indexed line number for a character offset."""
    return text.count("\n", 0, index) + 1


def make(fn, path, line, snippet):
    return Finding(fn.rule_id, fn.severity, path, line, snippet.strip(),
                   fn.description, fn.source)


def scan_text(text, path):
    findings = []
    for fn in RULES.values():
        findings.extend(fn(text, path))
    return sorted(findings, key=lambda f: (f.line, f.rule))


def scan_paths(paths):
    findings, unreadable = [], []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            targets = sorted(q for q in p.rglob("*")
                             if q.suffix.lower() in SCANNED_SUFFIXES)
        else:
            targets = [p]
        for target in targets:
            try:
                text = Path(target).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                unreadable.append(str(target))
                continue
            findings.extend(scan_text(text, str(target)))
    return findings, unreadable



# ---------------------------------------------------------------------------
# Token and colour rules. Every `source` names the brands/alan-personal.md
# section it encodes, so a brand change that invalidates a rule is greppable.
# ---------------------------------------------------------------------------

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
SITE_TOKENS = re.compile(r"--(?:swatch-\d+|life-\d+|dot-twinkle)\b")
STATUS_TOKENS = re.compile(r"var\(\s*--(good|warning|critical)\s*\)")
MARK_PROPS = re.compile(
    r"\b(fill|stroke|color|border(?:-[a-z]+)?-color)\s*:\s*([^;}\n]+)", re.I)
BRAND = "brands/alan-personal.md"


def _token_blocks(text):
    """Character ranges of blocks that legitimately define raw token values:
    :root{...} and [data-theme=...]{...}. Everything else must use var()."""
    spans = []
    for m in re.finditer(r"(:root[^{]*|\[data-theme[^{]*)\{", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    spans.append((m.start(), i))
                    break
            i += 1
    return spans


def _in_spans(index, spans):
    return any(lo <= index <= hi for lo, hi in spans)


@rule("site-token-names", "error",
      "The site's internal token names name site machinery, not this brand. "
      "Use --series-1..5 and --series-other.",
      f"{BRAND} > NOT this brand (denylist)")
def site_token_names(text, path):
    return [make(site_token_names, path, line_of(text, m.start()), m.group(0))
            for m in SITE_TOKENS.finditer(text)]


@rule("raw-hex", "error",
      "Colour comes from tokens, always. A hex literal outside a :root or "
      "[data-theme] block bypasses the palette.",
      f"{BRAND} > NOT this brand (denylist)")
def raw_hex(text, path):
    spans = _token_blocks(text)
    return [make(raw_hex, path, line_of(text, m.start()), m.group(0))
            for m in HEX.finditer(text) if not _in_spans(m.start(), spans)]


@rule("pure-black-on-white", "error",
      "--ink on --paper is the pairing. Pure #000 on #fff is not this brand.",
      f"{BRAND} > NOT this brand (denylist)")
def pure_black_on_white(text, path):
    out = []
    for m in re.finditer(r"\{[^}]*\}", text):
        block = m.group(0)
        black = re.search(r"color\s*:\s*(#(?:000|000000)|black)\b", block, re.I)
        white = re.search(r"background(?:-color)?\s*:\s*(#(?:fff|ffffff)|white)\b",
                          block, re.I)
        if black and white:
            out.append(make(pure_black_on_white, path,
                            line_of(text, m.start() + black.start()), black.group(0)))
    return out


@rule("accent-bright-as-mark", "error",
      "--accent-bright measures 2.49:1 on light paper. Gradient partner only — "
      "never a line, mark, label or series colour on light ground.",
      f"{BRAND} > Colors")
def accent_bright_as_mark(text, path):
    out = []
    for m in MARK_PROPS.finditer(text):
        value = m.group(2)
        if "gradient(" in value:
            continue
        if "--accent-bright" in value or re.search(r"#10b981\b", value, re.I):
            out.append(make(accent_bright_as_mark, path,
                            line_of(text, m.start()), m.group(0)))
    return out


@rule("status-as-series", "error",
      "Status colours are reserved. They are never series colours and never brand.",
      f"{BRAND} > Status colours")
def status_as_series(text, path):
    out = []
    for m in MARK_PROPS.finditer(text):
        if STATUS_TOKENS.search(m.group(2)):
            out.append(make(status_as_series, path,
                            line_of(text, m.start()), m.group(0)))
    for m in re.finditer(r"--series-\d+\s*:\s*([^;}\n]+)", text):
        if STATUS_TOKENS.search(m.group(1)):
            out.append(make(status_as_series, path,
                            line_of(text, m.start()), m.group(0)))
    return out


@rule("seventh-series-colour", "error",
      "Six categories is the ceiling. Past that: facet, or switch to a "
      "sequential scheme. 'Other' is the neutral residual, not slot 6.",
      f"{BRAND} > Series palette")
def seventh_series_colour(text, path):
    seen = {}
    for m in re.finditer(r"--series-(\d+)\s*:", text):
        seen.setdefault(int(m.group(1)), m.start())
    # Six categories is the ceiling; --series-6 is legal. The SEVENTH is not.
    extra = sorted(n for n in seen if n >= 7)
    return [make(seventh_series_colour, path, line_of(text, seen[n]),
                 f"--series-{n}") for n in extra]



# ---------------------------------------------------------------------------
# Theming rules.
# ---------------------------------------------------------------------------

THEME_STAMP = re.compile(r"data-theme|dataset\s*\.\s*theme")
THEME_ON_ROOT = re.compile(
    r"documentElement\s*\.\s*setAttribute\s*\(\s*['\"]data-theme['\"]"
    r"|documentElement\s*\.\s*dataset\s*\.\s*theme")


@rule("light-dark-with-theme-stamp", "error",
      "light-dark() responds only to color-scheme and cannot see a data-theme "
      "stamp. It compiles and silently ignores the toggle. Declare the tokens "
      "three times instead.",
      f"{BRAND} > NOT this brand (denylist)")
def light_dark_with_theme_stamp(text, path):
    # The stamp has two spellings: the attribute form (data-theme, in CSS
    # selectors and HTML) and the JS property form (dataset.theme). Either one
    # means a toggle exists that light-dark() cannot see.
    if not THEME_STAMP.search(text):
        return []
    return [make(light_dark_with_theme_stamp, path, line_of(text, m.start()),
                 m.group(0))
            for m in re.finditer(r"light-dark\s*\(", text)]


@rule("theme-on-root", "error",
      "Writing data-theme onto the document root pins a ground globally. "
      "Scope it to a container, and re-declare color and background on it.",
      f"{BRAND} > NOT this brand (denylist)")
def theme_on_root(text, path):
    return [make(theme_on_root, path, line_of(text, m.start()), m.group(0))
            for m in THEME_ON_ROOT.finditer(text)]



# ---------------------------------------------------------------------------
# SVG sizing rule.
#
# Scope note: this checks the inline <svg> tag only. An SVG sized from a
# stylesheet rule elsewhere in the file is not caught — deliberately. Resolving
# the cascade is the judgment case design.md excluded, and widening this without
# it would produce false positives. A design linter that cries wolf gets ignored.
# ---------------------------------------------------------------------------


@rule("svg-no-min-width", "error",
      "A fixed-viewBox SVG at width:100% with no min-width scales its labels "
      "with the container and goes illegible on a phone. Give it a min-width "
      "(~600px) and let its wrapper scroll.",
      f"{BRAND} > Infographics & diagrams")
def svg_no_min_width(text, path):
    out = []
    for m in re.finditer(r"<svg\b[^>]*>", text, re.I):
        tag = m.group(0)
        if "viewbox" not in tag.lower():
            continue
        # Matches both the style form (width:100%) and the attribute form
        # (width="100%").
        if not re.search(r"width\s*[:=]\s*[\"']?\s*100%", tag, re.I):
            continue
        if re.search(r"min-width", tag, re.I):
            continue
        out.append(make(svg_no_min_width, path, line_of(text, m.start()), tag))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    findings, unreadable = scan_paths(args.paths)

    if args.as_json:
        print(json.dumps([f._asdict() for f in findings], indent=2))
    elif not args.quiet:
        for f in findings:
            print(f"\n{f.file}:{f.line}\n  [{f.rule}] {f.snippet}\n    -> {f.description}",
                  file=sys.stderr)
        print(f"\n{len(findings)} finding(s).", file=sys.stderr)
    for path in unreadable:
        print(f"error: could not scan {path}", file=sys.stderr)

    if unreadable:
        return 1
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
