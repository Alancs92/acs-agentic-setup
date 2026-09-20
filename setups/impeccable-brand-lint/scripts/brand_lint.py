#!/usr/bin/env python3
"""Lint HTML/CSS against a brand's mechanically-testable denylist.

The engine holds no brand's values. Five rules are universal (they encode
structural mistakes any design system would call a mistake); four are
parameterised by a *brand profile* — a small JSON file under brands/ naming
that brand's forbidden token patterns, gradient-only colours, status tokens and
series ceiling.

A profile records the sha256 of the brand markdown it was derived from, so the
linter can refuse to run against a brand file that changed without the profile
being re-reviewed. That is the sync mechanism: explicit, not magic.

Exit codes match Impeccable's so both tools compose in one CI step:
  0  no findings
  1  a target could not be scanned, or the brand profile is stale
  2  findings
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

Finding = namedtuple("Finding", "rule severity file line snippet description source")

SCANNED_SUFFIXES = {".html", ".htm", ".css", ".svg"}
RULES = {}

BRANDS_DIR = Path(__file__).resolve().parent.parent / "brands"

# Used when no --brand is given: the five universal rules only. Every
# brand-parameterised rule reads an empty collection here and returns nothing,
# so an unconfigured run is honest rather than accidentally Casenote-shaped.
DEFAULT_PROFILE = {
    "name": "generic",
    "series": {"prefix": None, "ceiling": None},
    "gradient_only": [],
    "status_tokens": [],
    "forbidden_tokens": [],
    "impeccable": {"ignoreRules": []},
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_profile(name_or_path):
    """Resolve a brand profile by bare name (brands/<name>.json) or by path."""
    p = Path(name_or_path)
    if not p.suffix:
        p = BRANDS_DIR / f"{name_or_path}.json"
    if not p.is_file():
        raise FileNotFoundError(f"no brand profile at {p}")
    profile = json.loads(p.read_text())
    merged = dict(DEFAULT_PROFILE)
    merged.update(profile)
    return merged


# brands/ lives at <repo>/setups/<setup>/brands, so the repo root is three up.
REPO_ROOT = BRANDS_DIR.parents[2]


def _source_path(profile):
    """Resolve a profile's source_file.

    Absolute and ~-prefixed paths are used as given. A relative path is
    anchored to $BRAND_GUIDELINES_DIR when set, otherwise to the repo root —
    never to the process cwd, which would make the staleness check pass from
    one directory and raise a false alarm from another.
    """
    raw = profile.get("source_file")
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    base = os.environ.get("BRAND_GUIDELINES_DIR")
    if base:
        return Path(base).expanduser() / raw
    return REPO_ROOT / raw


def check_source(profile):
    """Return None when the profile matches its brand file, else a message.

    A profile with no recorded hash is not checked — that is a deliberate
    opt-out for a brand whose markdown is not on this machine.
    """
    expected = profile.get("source_sha256")
    if not expected:
        return None
    src = _source_path(profile)
    if src is None or not src.is_file():
        return (f"brand source {src or profile.get('source_file')!r} is not "
                "readable; cannot confirm this profile is current")
    actual = sha256_text(src.read_text())
    if actual != expected:
        return (f"{src} changed since this profile was reviewed "
                f"(recorded {expected[:12]}…, found {actual[:12]}…). "
                "Re-check the profile against the brand file, then update "
                "source_sha256.")
    return None


def impeccable_config(profile):
    """The .impeccable/config.json this brand implies, so one profile drives
    both tools."""
    ignore = list(profile.get("impeccable", {}).get("ignoreRules", []))
    return {
        "//": f"Generated from brand profile {profile.get('name')!r} by "
              "brand_lint.py --emit-impeccable-config. Do not hand-edit.",
        "detector": {"ignoreRules": ignore},
    }


def rule(rule_id, severity, description, source):
    """Register a rule. Signature: (text, path, profile) -> list[Finding]."""
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


def scan_text(text, path, profile=None):
    profile = profile if profile is not None else DEFAULT_PROFILE
    findings = []
    for fn in RULES.values():
        findings.extend(fn(text, path, profile))
    return sorted(findings, key=lambda f: (f.line, f.rule))


def scan_paths(paths, profile=None):
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
            findings.extend(scan_text(text, str(target), profile))
    return findings, unreadable



# ---------------------------------------------------------------------------
# Rules.
#
# UNIVERSAL rules encode mistakes any design system would call a mistake; they
# take `profile` and ignore it. BRAND rules read their values from the profile
# and return nothing when the profile does not configure them — so the engine
# itself carries no brand's palette.
# ---------------------------------------------------------------------------

# A hex colour, excluding two lookalikes that are not colours:
#   &#8321;        HTML numeric entity (subscript digits in axis labels)
#   href="#abc123" URL fragment
# Both appear in real pages; firing on them trains the reader to ignore the tool.
HEX = re.compile(r"(?<![&\"'])#[0-9a-fA-F]{3,8}\b")
MARK_PROPS = re.compile(
    r"\b(fill|stroke|color|border(?:-[a-z]+)?-color)\s*:\s*([^;}\n]+)", re.I)
# Series marks live in inline SVG. color/background/border-* are UI chrome,
# which is exactly where a status colour is supposed to appear.
SERIES_SURFACE = re.compile(r"\b(fill|stroke)\s*:\s*([^;}\n]+)", re.I)
THEME_STAMP = re.compile(r"data-theme|dataset\s*\.\s*theme")
THEME_ON_ROOT = re.compile(
    r"documentElement\s*\.\s*setAttribute\s*\(\s*['\"]data-theme['\"]"
    r"|documentElement\s*\.\s*dataset\s*\.\s*theme")
UNIVERSAL = "universal — structural, not brand-specific"


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


def _value_refs(value, needles):
    """True when a CSS value references any of `needles` (token or hex)."""
    for n in needles:
        if n.startswith("#"):
            if re.search(re.escape(n) + r"\b", value, re.I):
                return True
        elif n in value:
            return True
    return False


# --- universal --------------------------------------------------------------

@rule("raw-hex", "error",
      "Colour comes from tokens. A hex literal outside a :root or [data-theme] "
      "block bypasses the palette.", UNIVERSAL)
def raw_hex(text, path, profile):
    spans = _token_blocks(text)
    return [make(raw_hex, path, line_of(text, m.start()), m.group(0))
            for m in HEX.finditer(text) if not _in_spans(m.start(), spans)]


@rule("pure-black-on-white", "error",
      "Pure #000 on #fff is a default, not a decision. Tint both ends.",
      UNIVERSAL)
def pure_black_on_white(text, path, profile):
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


@rule("light-dark-with-theme-stamp", "error",
      "light-dark() responds only to color-scheme and cannot see a data-theme "
      "stamp. It compiles and silently ignores the toggle. Declare the tokens "
      "three times instead.", UNIVERSAL)
def light_dark_with_theme_stamp(text, path, profile):
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
      UNIVERSAL)
def theme_on_root(text, path, profile):
    return [make(theme_on_root, path, line_of(text, m.start()), m.group(0))
            for m in THEME_ON_ROOT.finditer(text)]


@rule("svg-no-min-width", "error",
      "A fixed-viewBox SVG at width:100% with no min-width scales its labels "
      "with the container and goes illegible on a phone. Give it a min-width "
      "and let its wrapper scroll.", UNIVERSAL)
def svg_no_min_width(text, path, profile):
    # Scope note: the inline tag only. An SVG sized from a stylesheet rule
    # elsewhere is not caught — resolving the cascade is a judgment case, and
    # widening this without it produces false positives.
    out = []
    for m in re.finditer(r"<svg\b[^>]*>", text, re.I):
        tag = m.group(0)
        if "viewbox" not in tag.lower():
            continue
        if not re.search(r"width\s*[:=]\s*[\"']?\s*100%", tag, re.I):
            continue
        if re.search(r"min-width", tag, re.I):
            continue
        out.append(make(svg_no_min_width, path, line_of(text, m.start()), tag))
    return out


# --- brand-parameterised ----------------------------------------------------

@rule("forbidden-token-names", "error",
      "This token name belongs to another system, not this brand's palette.",
      "profile.forbidden_tokens")
def forbidden_token_names(text, path, profile):
    patterns = profile.get("forbidden_tokens") or []
    out = []
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            out.append(make(forbidden_token_names, path,
                            line_of(text, m.start()), m.group(0)))
    return out


@rule("gradient-only-as-mark", "error",
      "This colour is a gradient partner only — it fails contrast as a mark. "
      "Never a line, mark, label or series colour.",
      "profile.gradient_only")
def gradient_only_as_mark(text, path, profile):
    needles = profile.get("gradient_only") or []
    if not needles:
        return []
    out = []
    for m in MARK_PROPS.finditer(text):
        value = m.group(2)
        if "gradient(" in value:
            continue
        if _value_refs(value, needles):
            out.append(make(gradient_only_as_mark, path,
                            line_of(text, m.start()), m.group(0)))
    return out


@rule("status-as-series", "error",
      "Status colours are reserved. They are never series colours. Using one on "
      "a chart mark reads as a category rather than a state.",
      "profile.status_tokens")
def status_as_series(text, path, profile):
    tokens = profile.get("status_tokens") or []
    if not tokens:
        return []
    alternation = "|".join(re.escape(t.lstrip("-")) for t in tokens)
    status_ref = re.compile(r"var\(\s*--(?:" + alternation + r")\s*\)")
    prefix = (profile.get("series") or {}).get("prefix")
    out = []
    # Only the mark surface. A status colour on warning TEXT or a warning
    # BORDER is the triad doing its job, not a categorical misuse.
    for m in SERIES_SURFACE.finditer(text):
        if status_ref.search(m.group(2)):
            out.append(make(status_as_series, path,
                            line_of(text, m.start()), m.group(0)))
    if prefix:
        pat = re.escape(prefix) + r"\d+\s*:\s*([^;}\n]+)"
        for m in re.finditer(pat, text):
            if status_ref.search(m.group(1)):
                out.append(make(status_as_series, path,
                                line_of(text, m.start()), m.group(0)))
    return out


@rule("series-ceiling", "error",
      "Past this brand's categorical ceiling: facet, or switch to a sequential "
      "scheme. The residual bucket is not the next slot.",
      "profile.series.ceiling")
def series_ceiling(text, path, profile):
    series = profile.get("series") or {}
    prefix, ceiling = series.get("prefix"), series.get("ceiling")
    if not prefix or not ceiling:
        return []
    seen = {}
    for m in re.finditer(re.escape(prefix) + r"(\d+)\s*:", text):
        seen.setdefault(int(m.group(1)), m.start())
    over = sorted(n for n in seen if n > ceiling)
    return [make(series_ceiling, path, line_of(text, seen[n]), f"{prefix}{n}")
            for n in over]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--brand", help="brand profile: a name under brands/ or a path. "
                                    "Omitted, only the universal rules run.")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-check-source", action="store_true",
                    help="skip the brand-file staleness check")
    ap.add_argument("--emit-impeccable-config", action="store_true",
                    help="print the .impeccable/config.json this brand implies")
    ap.add_argument("--list-rules", action="store_true",
                    help="print each rule and where its values come from")
    args = ap.parse_args(argv)

    if args.list_rules:
        for rule_id, fn in sorted(RULES.items()):
            print(f"{rule_id:28} {fn.source}")
        return 0

    try:
        profile = load_profile(args.brand) if args.brand else dict(DEFAULT_PROFILE)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    if not args.no_check_source:
        drift = check_source(profile)
        if drift:
            print(f"error: {drift}", file=sys.stderr)
            return 1

    if args.emit_impeccable_config:
        print(json.dumps(impeccable_config(profile), indent=2))
        return 0

    if not args.paths:
        ap.error("no paths given")

    findings, unreadable = scan_paths(args.paths, profile)

    if args.as_json:
        print(json.dumps([f._asdict() for f in findings], indent=2))
    elif not args.quiet:
        for f in findings:
            print(f"\n{f.file}:{f.line}\n  [{f.rule}] {f.snippet}\n    -> {f.description}",
                  file=sys.stderr)
        print(f"\n{len(findings)} finding(s) [brand: {profile.get('name')}].",
              file=sys.stderr)
    for path in unreadable:
        print(f"error: could not scan {path}", file=sys.stderr)

    if unreadable:
        return 1
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
