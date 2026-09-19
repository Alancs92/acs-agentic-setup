# Casenote Design Linting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two runnable checks for personal projects — a version-pinned, provenance-recorded Impeccable engine, and `casenote_lint.py` encoding the mechanically-testable half of Casenote's denylist — sharing one exit-code contract so both run in a single CI step.

**Architecture:** Two stdlib-only Python scripts in `scripts/`, mirroring how `claude-code-usage-stats` splits focused modules with co-located `test_*.py`. `fetch_engine.py` resolves the pinned engine from npm, verifies it, and writes `engine.lock.json`; `casenote_lint.py` is a rule registry over HTML/CSS text, each rule a pure function returning findings. Neither tool reads the other; they compose only through exit codes and a shared JSON finding shape.

**Tech Stack:** Python 3 standard library only (`subprocess`, `tarfile`, `hashlib`, `base64`, `json`, `re`, `argparse`, `unittest`). `npm` is invoked as a subprocess for engine acquisition — it is already required to run Impeccable at all. No pip installs, no third-party imports.

**Status:** complete, and **superseded in part**. All six tasks were implemented
and committed 2026-09-20 (`b195eca`..`4ee8cfd`). The setup was then revised to be
brand-agnostic: `casenote_lint.py` became `brand_lint.py`, four rules moved their
values into `brands/*.json` profiles, and the setup was renamed. This document is
kept as the record of how v1 was built — read `design.md`'s "Revision: making it
themeless" for what changed and why. Rule ids and file names below are v1's.

**Spec:** `setups/impeccable-casenote/design.md` (read it before starting — every task below argues from it)

## Global Constraints

- **Python 3 standard library only.** No pip installs, no third-party imports. Matches `apply_profile.py` and the `claude-code-usage-stats` scripts.
- **Exit-code contract, both tools:** `0` no findings, `1` a target could not be scanned (operational failure, takes precedence over findings), `2` findings. Copied from upstream Impeccable so the two compose.
- **`brands/alan-personal.md` is the single source of truth for token values.** No rule may restate a hex that the brand file owns without a `source` field naming the brand-file section it encodes. Never invent a token value.
- **Every rule carries `source`.** A brand change that invalidates a rule must be findable by grepping the rule's `source` string.
- **Never rewrite source files.** `casenote_lint.py` reads only.
- **`fetch_engine.py` fails closed.** Missing integrity, empty integrity, or hash mismatch aborts without writing anything.
- **The binary is never committed.** `engine.lock.json` is committed; the binary is gitignored.
- **Two version numbers.** npm CLI package version (`4.1.0`) and engine version (`0.1.5`, from the CLI's `optionalDependencies`) are distinct. The lock records both.
- **Unparseable file is a `1`, never a silent `0`.**

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/engine.lock.json` | Committed provenance: CLI version, engine version, platform, binary sha256, acquisition route, date. |
| `scripts/fetch_engine.py` | Resolve the pinned engine to a local binary; verify; write/check the lock; emit the `IMPECCABLE_BIN` export line. |
| `scripts/test_fetch_engine.py` | Tests for lock round-trip, integrity verification, and tamper detection. |
| `scripts/casenote_lint.py` | Finding model, rule registry, file walker, CLI, exit codes, `--json`. |
| `scripts/test_casenote_lint.py` | One clean-fixture test plus one negative test per rule, exit-code tests, JSON shape test. |
| `scripts/fixtures/casenote_clean.html` | Casenote-conformant specimen. Must stay at zero findings — the regression anchor. |
| `scripts/fixtures/*_bad.html` | One fixture per rule, each violating exactly one denylist item. |
| `config/impeccable.json` | The `.impeccable/config.json` template: `ignoreRules` plus the reason. |
| `README.md` | What/why/reproduce, including the three Evidence probes from the design. |
| `INDEX.md`, `scripts/INDEX.md`, `config/INDEX.md`, `scripts/fixtures/INDEX.md` | Navigation contract. |

**Rule registry shape** (defined in Task 2, used by Tasks 3-5):

```python
Finding = namedtuple("Finding", "rule severity file line snippet description source")
# A rule is: Callable[[str, str], list[Finding]]  ->  (text, path) -> findings
RULES = {}  # rule_id -> function, populated by the @rule decorator
```

---

### Task 1: Pinned engine acquisition and provenance lock

**Files:**
- Create: `setups/impeccable-casenote/scripts/fetch_engine.py`
- Create: `setups/impeccable-casenote/scripts/test_fetch_engine.py`
- Create: `setups/impeccable-casenote/scripts/engine.lock.json`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `load_lock(path) -> dict`, `verify_binary(path, expected_sha256) -> bool`, `sha256_file(path) -> str`, `npm_integrity(pkg, version) -> str`, `verify_tarball(data, integrity) -> bool`, `platform_target() -> str`, `resolve(lock, dest_dir, *, npm_runner=subprocess.run) -> Path`. Nothing later depends on these; this task is self-contained.

- [x] **Step 1: Write the failing tests**

```python
# scripts/test_fetch_engine.py
import base64, hashlib, json, tempfile, unittest
from pathlib import Path

import fetch_engine


class TestHashing(unittest.TestCase):
    def test_sha256_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "blob"
            p.write_bytes(b"impeccable")
            self.assertEqual(
                fetch_engine.sha256_file(p),
                hashlib.sha256(b"impeccable").hexdigest(),
            )

    def test_verify_tarball_accepts_matching_sri(self):
        data = b"tarball-bytes"
        sri = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        self.assertTrue(fetch_engine.verify_tarball(data, sri))

    def test_verify_tarball_rejects_tampered_bytes(self):
        sri = "sha512-" + base64.b64encode(hashlib.sha512(b"good").digest()).decode()
        self.assertFalse(fetch_engine.verify_tarball(b"evil", sri))

    def test_verify_tarball_rejects_empty_integrity(self):
        self.assertFalse(fetch_engine.verify_tarball(b"anything", ""))

    def test_verify_tarball_rejects_unknown_algorithm(self):
        self.assertFalse(fetch_engine.verify_tarball(b"x", "md5-abc123"))


class TestLock(unittest.TestCase):
    def test_load_lock_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "engine.lock.json"
            p.write_text(json.dumps({
                "cli_version": "4.1.0",
                "engine_version": "0.1.5",
                "platform": "darwin-arm64",
                "binary_sha256": "ab" * 32,
                "route": "npm",
            }))
            lock = fetch_engine.load_lock(p)
            self.assertEqual(lock["engine_version"], "0.1.5")
            self.assertEqual(lock["cli_version"], "4.1.0")

    def test_verify_binary_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "impeccable"
            p.write_bytes(b"original")
            good = fetch_engine.sha256_file(p)
            self.assertTrue(fetch_engine.verify_binary(p, good))
            p.write_bytes(b"tampered")
            self.assertFalse(fetch_engine.verify_binary(p, good))

    def test_verify_binary_missing_file_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(fetch_engine.verify_binary(Path(d) / "nope", "ab" * 32))


class TestPlatform(unittest.TestCase):
    def test_platform_target_shape(self):
        target = fetch_engine.platform_target()
        self.assertRegex(target, r"^(darwin|linux|windows)-(arm64|x64)$")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd setups/impeccable-casenote/scripts && uv run --with pytest python -m pytest test_fetch_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fetch_engine'`

- [x] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Resolve the pinned Impeccable engine to a verified local binary.

Provenance lives in engine.lock.json (committed). The binary itself is not
committed. Fails closed: missing, empty, or mismatched integrity aborts
without writing anything.

Primary route is the platform npm package, whose immutable version and
dist.integrity sha512 are a stronger provenance root than a mutable GitHub
release asset. See design.md, Evidence 3.
"""
import argparse
import base64
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEFAULT_LOCK = Path(__file__).with_name("engine.lock.json")
PKG_PREFIX = "@impeccable/cli-"


def platform_target() -> str:
    """The <os>-<arch> string npm and the upstream shim both use."""
    os_name = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(
        platform.machine(), platform.machine()
    )
    return f"{os_name}-{arch}"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tarball(data: bytes, integrity: str) -> bool:
    """Check bytes against an npm Subresource Integrity string (sha512-<b64>)."""
    if not integrity or "-" not in integrity:
        return False
    algo, _, b64 = integrity.partition("-")
    if algo != "sha512":
        return False
    try:
        expected = base64.b64decode(b64, validate=True)
    except Exception:
        return False
    return hashlib.sha512(data).digest() == expected


def verify_binary(path, expected_sha256: str) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    return sha256_file(p) == expected_sha256


def load_lock(path=DEFAULT_LOCK) -> dict:
    return json.loads(Path(path).read_text())


def npm_integrity(pkg: str, version: str, runner=subprocess.run) -> str:
    """Ask the registry for the published dist.integrity of an exact version."""
    out = runner(
        ["npm", "view", f"{pkg}@{version}", "dist.integrity", "--json"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return json.loads(out) if out.startswith('"') else out


def resolve(lock: dict, dest_dir, *, runner=subprocess.run) -> Path:
    """Fetch, verify and install the locked engine. Returns the binary path."""
    target = lock.get("platform") or platform_target()
    pkg = f"{PKG_PREFIX}{target}"
    version = lock["engine_version"]
    dest = Path(dest_dir) / lock["engine_version"] / "impeccable"

    if dest.is_file() and verify_binary(dest, lock.get("binary_sha256", "")):
        return dest

    integrity = npm_integrity(pkg, version, runner=runner)
    with tempfile.TemporaryDirectory() as tmp:
        runner(["npm", "pack", f"{pkg}@{version}", "--silent"],
               cwd=tmp, capture_output=True, text=True, check=True)
        tarballs = list(Path(tmp).glob("*.tgz"))
        if len(tarballs) != 1:
            raise SystemExit(f"expected one tarball, got {len(tarballs)}")
        data = tarballs[0].read_bytes()
        if not verify_tarball(data, integrity):
            raise SystemExit(
                f"integrity mismatch for {pkg}@{version}; refusing the unverified download"
            )
        with tarfile.open(tarballs[0]) as tf:
            member = tf.extractfile("package/bin/impeccable")
            if member is None:
                raise SystemExit("tarball has no package/bin/impeccable")
            payload = member.read()

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".part")
    tmp_path.write_bytes(payload)
    tmp_path.chmod(0o755)
    tmp_path.replace(dest)

    expected = lock.get("binary_sha256")
    actual = sha256_file(dest)
    if expected and expected != actual:
        dest.unlink()
        raise SystemExit(f"binary sha256 mismatch: locked {expected}, got {actual}")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", default=DEFAULT_LOCK, type=Path)
    ap.add_argument("--dest", default=Path.home() / ".impeccable" / "bin", type=Path)
    ap.add_argument("--print-export", action="store_true",
                    help="print the IMPECCABLE_BIN export line and exit")
    args = ap.parse_args(argv)

    lock = load_lock(args.lock)
    try:
        binary = resolve(lock, args.dest)
    except SystemExit as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if args.print_export:
        print(f'export IMPECCABLE_BIN="{binary}"')
    else:
        print(f"engine {lock['engine_version']} verified at {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd setups/impeccable-casenote/scripts && uv run --with pytest python -m pytest test_fetch_engine.py -q`
Expected: PASS (9 tests)

- [x] **Step 5: Generate the real lock file**

Run the resolver once against the live registry, then record what it produced:

```bash
cd setups/impeccable-casenote/scripts
cat > engine.lock.json <<'JSON'
{
  "cli_version": "4.1.0",
  "engine_version": "0.1.5",
  "platform": "darwin-arm64",
  "binary_sha256": "",
  "route": "npm:@impeccable/cli-darwin-arm64",
  "acquired": "2026-09-20"
}
JSON
python3 fetch_engine.py --print-export
# Take the sha256 the run reports and write it into binary_sha256, then
# re-run: a second run must short-circuit on the verified binary.
python3 -c "import fetch_engine,json,pathlib; \
lock=fetch_engine.load_lock(); \
b=pathlib.Path.home()/'.impeccable'/'bin'/lock['engine_version']/'impeccable'; \
print(fetch_engine.sha256_file(b))"
```

Verify the re-run is a no-op (no second download) and that corrupting the
binary makes it re-fetch.

- [x] **Step 6: Gitignore the binary, commit**

```bash
cd /Users/alan.soewargo@harrison.ai/repos/acs-agentic-setup/feat-claude-code-usage-stats
printf '\n# Resolved Impeccable engine — provenance is engine.lock.json, not the binary\n.impeccable/\n__pycache__/\n' >> .gitignore
git add .gitignore \
  setups/impeccable-casenote/scripts/fetch_engine.py \
  setups/impeccable-casenote/scripts/test_fetch_engine.py \
  setups/impeccable-casenote/scripts/engine.lock.json
git commit -m "feat(impeccable-casenote): pinned engine acquisition with provenance lock"
```

> This step also closes the `__pycache__` gitignore gap recorded in
> `research/impeccable-design-skill.md`.

---

### Task 2: Lint core — finding model, registry, walker, CLI

**Files:**
- Create: `setups/impeccable-casenote/scripts/casenote_lint.py`
- Create: `setups/impeccable-casenote/scripts/test_casenote_lint.py`
- Create: `setups/impeccable-casenote/scripts/fixtures/casenote_clean.html`

**Interfaces:**
- Consumes: nothing from Task 1 (the two tools are independent).
- Produces: `Finding` namedtuple (`rule severity file line snippet description source`); `@rule(rule_id, severity, description, source)` decorator registering into `RULES`; `scan_text(text, path) -> list[Finding]`; `scan_paths(paths) -> tuple[list[Finding], list[str]]` returning findings and unreadable paths; `main(argv) -> int`. Tasks 3-5 add rule functions only and change nothing here.

- [x] **Step 1: Write the clean fixture**

This is the regression anchor — it must stay at zero findings forever. It is
the same specimen that produced Evidence 1 in `design.md`.

```html
<!-- scripts/fixtures/casenote_clean.html -->
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Casenote Specimen</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400&display=swap">
<style>
:root{
  --paper:#fcfdfc; --surface:#f4f7f5; --ink:#102420; --body:#39433f;
  --muted:#5f6a64; --rule:#e5ebe8; --accent:#0f766e; --accent-bright:#10b981;
  --series-1:#0d9488; --series-2:#b45309; --series-3:#2563eb;
  --series-4:#9f2d55; --series-5:#6d28d9; --series-other:#475569;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  --font-head:"Inter",system-ui,sans-serif;
  --font-body:"Source Serif 4",Georgia,serif;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --paper:#0f1715; --ink:#e9f0ec; --body:#b8c4be; --accent:#2dd4bf;
}}
:root[data-theme="dark"]{
  --paper:#0f1715; --ink:#e9f0ec; --body:#b8c4be; --accent:#2dd4bf;
}
body{background:var(--paper);color:var(--body);font-family:var(--font-body);}
h1{font-family:var(--font-head);color:var(--ink);}
.hairline{height:3px;background:linear-gradient(90deg,var(--accent),var(--accent-bright));}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:4px;}
.mark-1{fill:var(--series-1);}
a{color:var(--accent);}
</style></head>
<body><div class="hairline"></div><h1>Specimen</h1>
<div class="card"><p>Body copy.</p><a href="#">A link</a></div>
<svg viewBox="0 0 600 300" style="width:100%;min-width:600px"><rect class="mark-1" width="10" height="10"/></svg>
</body></html>
```

- [x] **Step 2: Write the failing core tests**

```python
# scripts/test_casenote_lint.py
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

import casenote_lint

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).with_name("casenote_lint.py")


def run_cli(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class TestCore(unittest.TestCase):
    def test_clean_fixture_has_no_findings(self):
        path = FIXTURES / "casenote_clean.html"
        findings = casenote_lint.scan_text(path.read_text(), str(path))
        self.assertEqual(findings, [], f"clean fixture regressed: {findings}")

    def test_every_rule_declares_a_source(self):
        for rule_id, fn in casenote_lint.RULES.items():
            self.assertTrue(getattr(fn, "source", ""),
                            f"{rule_id} has no brand-file source")

    def test_scan_paths_reports_unreadable(self):
        findings, unreadable = casenote_lint.scan_paths([Path("/nonexistent.html")])
        self.assertEqual(findings, [])
        self.assertEqual(len(unreadable), 1)


class TestExitCodes(unittest.TestCase):
    def test_clean_exits_zero(self):
        code, _, _ = run_cli(str(FIXTURES / "casenote_clean.html"))
        self.assertEqual(code, 0)

    def test_unreadable_exits_one(self):
        code, _, _ = run_cli("/nonexistent.html")
        self.assertEqual(code, 1)

    def test_unreadable_takes_precedence_over_findings(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, _, _ = run_cli(str(bad), "/nonexistent.html")
            self.assertEqual(code, 1, "operational failure must beat findings")


class TestJsonShape(unittest.TestCase):
    def test_json_fields_match_upstream_shape(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.html"
            bad.write_text("<style>.x{--swatch-1:#fff;}</style>")
            code, out, _ = run_cli("--json", str(bad))
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertGreaterEqual(len(payload), 1)
            self.assertEqual(
                set(payload[0]),
                {"rule", "severity", "file", "line", "snippet", "description", "source"},
            )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 3: Run tests to verify they fail**

Run: `cd setups/impeccable-casenote/scripts && uv run --with pytest python -m pytest test_casenote_lint.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'casenote_lint'`

- [x] **Step 4: Write the core implementation**

```python
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
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd setups/impeccable-casenote/scripts && uv run --with pytest python -m pytest test_casenote_lint.py -q`
Expected: PASS. `test_json_shape` and the findings-related exit-code tests rely
on the `site-token-names` rule, which arrives in Task 3 — until then they FAIL.
Implement Task 3's `site-token-names` rule first if you want a green bar here,
or accept these three as red until Task 3 closes them.

- [x] **Step 6: Commit**

```bash
git add setups/impeccable-casenote/scripts/casenote_lint.py \
        setups/impeccable-casenote/scripts/test_casenote_lint.py \
        setups/impeccable-casenote/scripts/fixtures/casenote_clean.html
git commit -m "feat(impeccable-casenote): lint core — finding model, registry, CLI"
```

---

### Task 3: Token and colour rules

**Files:**
- Modify: `setups/impeccable-casenote/scripts/casenote_lint.py` (append rules)
- Modify: `setups/impeccable-casenote/scripts/test_casenote_lint.py` (append tests)
- Create: `scripts/fixtures/{site_tokens,raw_hex,pure_black,accent_bright,status_series,seventh_series}_bad.html`

**Interfaces:**
- Consumes: `@rule`, `Finding`, `make`, `line_of` from Task 2.
- Produces: rule ids `site-token-names`, `raw-hex`, `pure-black-on-white`, `accent-bright-as-mark`, `status-as-series`, `seventh-series-colour`. No new shared functions.

- [x] **Step 1: Write the six failing tests**

```python
# append to scripts/test_casenote_lint.py
class TestTokenRules(unittest.TestCase):
    def assert_flags(self, rule_id, fixture_name):
        path = FIXTURES / fixture_name
        findings = casenote_lint.scan_text(path.read_text(), str(path))
        ids = [f.rule for f in findings]
        self.assertIn(rule_id, ids, f"{fixture_name} did not trip {rule_id}: {ids}")

    def test_site_token_names(self):
        self.assert_flags("site-token-names", "site_tokens_bad.html")

    def test_raw_hex_outside_root(self):
        self.assert_flags("raw-hex", "raw_hex_bad.html")

    def test_pure_black_on_white(self):
        self.assert_flags("pure-black-on-white", "pure_black_bad.html")

    def test_accent_bright_as_mark(self):
        self.assert_flags("accent-bright-as-mark", "accent_bright_bad.html")

    def test_status_as_series(self):
        self.assert_flags("status-as-series", "status_series_bad.html")

    def test_seventh_series_colour(self):
        self.assert_flags("seventh-series-colour", "seventh_series_bad.html")
```

- [x] **Step 2: Write the six fixtures, each violating exactly one rule**

```bash
cd setups/impeccable-casenote/scripts/fixtures

cat > site_tokens_bad.html <<'EOF'
<style>:root{--swatch-1:#0d9488;--life-2:#b45309;--dot-twinkle:#fcfdfc;}</style>
EOF

cat > raw_hex_bad.html <<'EOF'
<style>
:root{--accent:#0f766e;}
.card{border:1px solid #e5ebe8;color:#102420;}
</style>
EOF

cat > pure_black_bad.html <<'EOF'
<style>body{color:#000000;background:#ffffff;}</style>
EOF

cat > accent_bright_bad.html <<'EOF'
<style>
:root{--accent-bright:#10b981;}
.legend-dot{fill:var(--accent-bright);}
.label{color:#10b981;}
</style>
EOF

cat > status_series_bad.html <<'EOF'
<style>
:root{--good:#0ca30c;--series-1:#0d9488;}
.bar-2{fill:var(--good);}
</style>
EOF

cat > seventh_series_bad.html <<'EOF'
<style>:root{
--series-1:#0d9488;--series-2:#b45309;--series-3:#2563eb;
--series-4:#9f2d55;--series-5:#6d28d9;--series-6:#77712f;--series-7:#2f7f80;
}</style>
EOF
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -k TokenRules -q`
Expected: FAIL — six failures, each "did not trip <rule>".

- [x] **Step 4: Implement the six rules**

```python
# append to scripts/casenote_lint.py, above main()

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
```

- [x] **Step 5: Run the full suite**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -q`
Expected: PASS, including the clean-fixture anchor and the three Task 2 tests
that were red. If `casenote_clean.html` now trips `raw-hex`, the `_token_blocks`
span logic is wrong — fix that, never the fixture.

- [x] **Step 6: Commit**

```bash
git add setups/impeccable-casenote/scripts/casenote_lint.py \
        setups/impeccable-casenote/scripts/test_casenote_lint.py \
        setups/impeccable-casenote/scripts/fixtures/
git commit -m "feat(impeccable-casenote): token and colour denylist rules"
```

---

### Task 4: Theming rules

**Files:**
- Modify: `setups/impeccable-casenote/scripts/casenote_lint.py`
- Modify: `setups/impeccable-casenote/scripts/test_casenote_lint.py`
- Create: `scripts/fixtures/{light_dark,theme_on_root}_bad.html`

**Interfaces:**
- Consumes: `@rule`, `make`, `line_of` from Task 2.
- Produces: rule ids `light-dark-with-theme-stamp`, `theme-on-root`.

- [x] **Step 1: Write the failing tests**

```python
# append to scripts/test_casenote_lint.py
class TestThemingRules(unittest.TestCase):
    def assert_flags(self, rule_id, fixture_name):
        path = FIXTURES / fixture_name
        findings = casenote_lint.scan_text(path.read_text(), str(path))
        ids = [f.rule for f in findings]
        self.assertIn(rule_id, ids, f"{fixture_name} did not trip {rule_id}: {ids}")

    def test_light_dark_with_theme_stamp(self):
        self.assert_flags("light-dark-with-theme-stamp", "light_dark_bad.html")

    def test_theme_on_root(self):
        self.assert_flags("theme-on-root", "theme_on_root_bad.html")

    def test_light_dark_alone_is_not_flagged(self):
        """light-dark() is fine when nothing stamps data-theme."""
        text = "<style>:root{color:light-dark(#102420,#e9f0ec);}</style>"
        ids = [f.rule for f in casenote_lint.scan_text(text, "x.html")]
        self.assertNotIn("light-dark-with-theme-stamp", ids)
```

- [x] **Step 2: Write the fixtures**

```bash
cd setups/impeccable-casenote/scripts/fixtures

cat > light_dark_bad.html <<'EOF'
<style>:root{color:light-dark(#102420,#e9f0ec);}</style>
<button onclick="toggle()">theme</button>
<script>function toggle(){document.querySelector('.page').dataset.theme='dark';}</script>
EOF

cat > theme_on_root_bad.html <<'EOF'
<script>document.documentElement.setAttribute('data-theme','dark');</script>
EOF
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -k ThemingRules -q`
Expected: FAIL — two "did not trip" failures; `test_light_dark_alone_is_not_flagged` already passes.

- [x] **Step 4: Implement the two rules**

```python
# append to scripts/casenote_lint.py, above main()

@rule("light-dark-with-theme-stamp", "error",
      "light-dark() responds only to color-scheme and cannot see a data-theme "
      "stamp. It compiles and silently ignores the toggle. Declare the tokens "
      "three times instead.",
      f"{BRAND} > NOT this brand (denylist)")
def light_dark_with_theme_stamp(text, path):
    if "data-theme" not in text:
        return []
    return [make(light_dark_with_theme_stamp, path, line_of(text, m.start()),
                 m.group(0))
            for m in re.finditer(r"light-dark\s*\(", text)]


@rule("theme-on-root", "error",
      "Writing data-theme onto the document root pins a ground globally. "
      "Scope it to a container, and re-declare color and background on it.",
      f"{BRAND} > NOT this brand (denylist)")
def theme_on_root(text, path):
    pattern = re.compile(
        r"document(?:Element)?\s*\.\s*documentElement\s*\.\s*"
        r"(?:setAttribute\s*\(\s*['\"]data-theme['\"]|dataset\s*\.\s*theme)"
        r"|documentElement\s*\.\s*setAttribute\s*\(\s*['\"]data-theme['\"]"
        r"|documentElement\s*\.\s*dataset\s*\.\s*theme")
    return [make(theme_on_root, path, line_of(text, m.start()), m.group(0))
            for m in pattern.finditer(text)]
```

- [x] **Step 5: Run the full suite**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -q`
Expected: PASS. The clean fixture stamps `data-theme` in CSS selectors but never
writes it from JS, and uses no `light-dark()` — it must stay at zero.

- [x] **Step 6: Commit**

```bash
git add setups/impeccable-casenote/scripts/casenote_lint.py \
        setups/impeccable-casenote/scripts/test_casenote_lint.py \
        setups/impeccable-casenote/scripts/fixtures/
git commit -m "feat(impeccable-casenote): theming denylist rules"
```

---

### Task 5: SVG sizing rule

**Files:**
- Modify: `setups/impeccable-casenote/scripts/casenote_lint.py`
- Modify: `setups/impeccable-casenote/scripts/test_casenote_lint.py`
- Create: `scripts/fixtures/svg_min_width_bad.html`

**Interfaces:**
- Consumes: `@rule`, `make`, `line_of` from Task 2.
- Produces: rule id `svg-no-min-width`.

- [x] **Step 1: Write the failing tests**

```python
# append to scripts/test_casenote_lint.py
class TestSvgRule(unittest.TestCase):
    def test_fixed_viewbox_without_min_width(self):
        path = FIXTURES / "svg_min_width_bad.html"
        ids = [f.rule for f in
               casenote_lint.scan_text(path.read_text(), str(path))]
        self.assertIn("svg-no-min-width", ids)

    def test_min_width_present_is_clean(self):
        text = '<svg viewBox="0 0 600 300" style="width:100%;min-width:600px"></svg>'
        ids = [f.rule for f in casenote_lint.scan_text(text, "x.html")]
        self.assertNotIn("svg-no-min-width", ids)

    def test_svg_without_viewbox_is_ignored(self):
        text = '<svg style="width:100%"></svg>'
        ids = [f.rule for f in casenote_lint.scan_text(text, "x.html")]
        self.assertNotIn("svg-no-min-width", ids)
```

- [x] **Step 2: Write the fixture**

```bash
cat > setups/impeccable-casenote/scripts/fixtures/svg_min_width_bad.html <<'EOF'
<svg viewBox="0 0 600 300" style="width:100%"><text x="10" y="20">Axis label</text></svg>
EOF
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -k SvgRule -q`
Expected: FAIL on `test_fixed_viewbox_without_min_width`; the other two pass already.

- [x] **Step 4: Implement the rule**

```python
# append to scripts/casenote_lint.py, above main()

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
        if not re.search(r"width\s*[:=]\s*[\"']?\s*100%", tag, re.I):
            continue
        if re.search(r"min-width", tag, re.I):
            continue
        out.append(make(svg_no_min_width, path, line_of(text, m.start()), tag))
    return out
```

> **Scope note.** This checks the inline tag only. An SVG sized from a
> stylesheet rule elsewhere in the file is not caught — deliberately, since
> resolving the cascade is the judgment case `design.md` excluded. Widening it
> without cascade resolution would produce false positives, and a design linter
> that cries wolf gets ignored.

- [x] **Step 5: Run the full suite**

Run: `uv run --with pytest python -m pytest test_casenote_lint.py -q`
Expected: PASS — all rules, all exit codes, clean fixture still at zero.

- [x] **Step 6: Commit**

```bash
git add setups/impeccable-casenote/scripts/casenote_lint.py \
        setups/impeccable-casenote/scripts/test_casenote_lint.py \
        setups/impeccable-casenote/scripts/fixtures/
git commit -m "feat(impeccable-casenote): SVG sizing denylist rule"
```

---

### Task 6: Config template, docs, and index wiring

**Files:**
- Create: `setups/impeccable-casenote/config/impeccable.json`
- Create: `setups/impeccable-casenote/config/INDEX.md`
- Create: `setups/impeccable-casenote/scripts/INDEX.md`
- Create: `setups/impeccable-casenote/scripts/fixtures/INDEX.md`
- Create: `setups/impeccable-casenote/README.md`
- Modify: `setups/impeccable-casenote/INDEX.md` (drop "designed, not implemented")
- Modify: `setups/INDEX.md` (status `designed` -> `active`)
- Modify: `research/impeccable-design-skill.md` (status -> concluded, link forward)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the CLI surfaces of `fetch_engine.py` and `casenote_lint.py` from Tasks 1-5. Documentation only; produces no code.

- [x] **Step 1: Write the config template**

```json
{
  "//": "Copy to <project>/.impeccable/config.json. See setups/impeccable-casenote/design.md.",
  "detector": {
    "ignoreRules": ["overused-font"],
    "//ignoreRules": [
      "overused-font: Casenote mandates Inter for headings and UI. That is a",
      "validated choice, not a default — these artifacts land in decks, PDFs and",
      "PPTX where webfonts do not resolve, and Inter degrades to the system stack",
      "without reflow. Source: brands/alan-personal.md > Typography.",
      "This is the ONLY rule Casenote disagrees with; see design.md Evidence 1."
    ]
  }
}
```

- [x] **Step 2: Write the three INDEX files**

```bash
cd setups/impeccable-casenote

cat > config/INDEX.md <<'EOF'
# setups/impeccable-casenote/config/

| File | What it covers |
|---|---|
| [`impeccable.json`](impeccable.json) | Template for a project's `.impeccable/config.json`. Suppresses `overused-font` — the single upstream rule Casenote disagrees with — and records why inline. |

Copy, don't symlink: each project owns its own `.impeccable/config.json`.
EOF

cat > scripts/INDEX.md <<'EOF'
# setups/impeccable-casenote/scripts/

| File | What it covers |
|---|---|
| [`fetch_engine.py`](fetch_engine.py) | Resolves the pinned Impeccable engine from npm, verifies integrity, records provenance. `--print-export` emits the `IMPECCABLE_BIN` line. |
| [`engine.lock.json`](engine.lock.json) | Committed provenance: CLI + engine versions, platform, binary sha256, route, date. The binary itself is gitignored. |
| [`casenote_lint.py`](casenote_lint.py) | Casenote denylist checker. Exit 0/1/2, `--json`, `--quiet`. |
| [`test_fetch_engine.py`](test_fetch_engine.py), [`test_casenote_lint.py`](test_casenote_lint.py) | Test suites. `uv run --with pytest python -m pytest -q` |
| [`fixtures/`](fixtures/INDEX.md) | Test fixtures: one clean specimen plus one violation per rule. |
EOF

cat > scripts/fixtures/INDEX.md <<'EOF'
# setups/impeccable-casenote/scripts/fixtures/

Test fixtures for `casenote_lint.py`.

- `casenote_clean.html` — Casenote-conformant specimen. **Must stay at zero
  findings.** If a new rule trips it, the rule is wrong, not the fixture.
- `*_bad.html` — one per rule, each violating exactly one denylist item so a
  failure names the rule that regressed.
EOF
```

- [x] **Step 3: Write the README**

Use `templates/setup-template.md` as the skeleton. It must contain:
- The four drivers and the authority-model table from `design.md`.
- The three Evidence probes **as runnable commands**, since every claim in the
  design rests on them and a reader must be able to re-verify.
- Usage: `python3 scripts/fetch_engine.py --print-export`, then
  `eval "$(...)"`, then `impeccable detect <path>` and
  `python3 scripts/casenote_lint.py <path>`.
- The combined CI snippet:

```bash
set -e
eval "$(python3 scripts/fetch_engine.py --print-export)"
impeccable detect --json src/ ; imp=$?
python3 scripts/casenote_lint.py --json src/ ; own=$?
[ $imp -eq 1 ] || [ $own -eq 1 ] && exit 1
[ $imp -eq 2 ] || [ $own -eq 2 ] && exit 2
exit 0
```

- The `dataviz` / `validate_palette.js` known gap, verbatim from `design.md`.
- A "Not for Harrison.ai repos" line, pending the supply-chain review.

- [x] **Step 4: Flip the statuses**

- `setups/impeccable-casenote/INDEX.md`: remove "**Status: designed, not yet
  implemented.** Only `design.md` exists so far." and list the real files.
- `setups/INDEX.md`: change this setup's Status cell from `designed` to `active`.
- `research/impeccable-design-skill.md`: status becomes
  `concluded → promoted to setups/impeccable-casenote`; add the forward link
  per the `research/INDEX.md` convention; update `research/INDEX.md`'s row too.

- [x] **Step 5: Add the CHANGELOG entry**

Append under a new dated heading, newest first: the setup went from designed to
implemented; name the two tools, the rule count, and that the engine is pinned
with committed provenance.

- [x] **Step 6: Verify end-to-end, then commit**

```bash
cd setups/impeccable-casenote
uv run --with pytest python -m pytest scripts/ -q          # all green
python3 scripts/casenote_lint.py scripts/fixtures/casenote_clean.html; echo $?   # 0
python3 scripts/casenote_lint.py scripts/fixtures/raw_hex_bad.html; echo $?      # 2
python3 scripts/casenote_lint.py /nonexistent.html; echo $?                      # 1
eval "$(python3 scripts/fetch_engine.py --print-export)" && "$IMPECCABLE_BIN" detect --help | head -3
```

```bash
git add setups/impeccable-casenote/config/ setups/impeccable-casenote/README.md \
        setups/impeccable-casenote/INDEX.md setups/impeccable-casenote/scripts/INDEX.md \
        setups/impeccable-casenote/scripts/fixtures/INDEX.md \
        setups/INDEX.md research/impeccable-design-skill.md research/INDEX.md CHANGELOG.md
git commit -m "docs(impeccable-casenote): config template, README, index wiring"
```

---

## Deferred to a follow-up

Recorded so they are not silently dropped:

- **`brand-guidelines` pointer line.** `design.md` calls for one line in
  `brands/alan-personal.md` (or `SKILL.md`) noting the denylist has a mechanical
  checker. That file is edited **only via `skill-improver`**, per its own header,
  so it is out of scope for this plan and needs its own pass.
- **The `dataviz` / `validate_palette.js` gap.** Casenote's cited palette
  validator is not resolvable on disk. Not this plan's job to replace, but it
  means `casenote_lint.py` is currently the only runnable check.
- **Adoption in a real project.** This plan builds the tools; wiring them into a
  personal project's CI is the step that proves them.
