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
