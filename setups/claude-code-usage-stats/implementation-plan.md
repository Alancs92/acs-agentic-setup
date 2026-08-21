# Claude Code Usage Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `claude-acs stats` — one command that walks every Claude Code account on the machine, emits a versioned `stats.json`, and renders a standalone HTML dashboard of prompt volume, cadence, tokens, models, tools and projects across accounts.

**Architecture:** Six focused stdlib-only Python modules in `scripts/`, invoked through a thin `collect_stats.py` CLI, mirroring how `claude-code-account-profiles` splits repo-as-truth logic from a `claude-acs` shell wrapper. Two data layers (prompt history, session transcripts) feed one aggregator that emits the schema; a renderer inlines that JSON into an HTML template so the dashboard opens over `file://` with no server. An incremental cache keyed on `(path, mtime_ns, size)` keeps repeat runs fast over ~2 GB of transcripts.

**Tech Stack:** Python 3 standard library only (no third-party deps, matching `apply_profile.py`); `zoneinfo` for the IANA timezone name; `unittest` for tests; vanilla JS + inline SVG for the dashboard; zsh for the `claude-acs` wrapper.

**Spec:** `setups/claude-code-usage-stats/design.md` (read it before starting — every task below argues from it)

## Global Constraints

- **Python 3 standard library only.** No pip installs, no third-party imports. Matches the existing `apply_profile.py` precedent in this repo.
- **All emitted times are UTC.** Dates and hours in `stats.json` are UTC; the collector's local zone is recorded once as an IANA name in the `timezone` field. Never write a local-time date or hour.
- **No per-prompt row is ever written to output.** No prompt text, no prompt hash, no per-prompt length. Prompts aggregate into `(account, date, hour, project)` buckets before serialisation. Prompt text exists only in memory.
- **No `duration` key anywhere in the output**, and the dashboard must never compute one. `sessionId` is reused across `--resume`; observed spans reach 977 hours.
- **No dollar-cost estimation.** Token counts only. No price table.
- **Dashboard has no chart library, no CDN request, no build step.** Vanilla JS and inline SVG only.
- **`schema_version` is `1`.** The dashboard refuses to render an unrecognised version rather than drawing a wrong chart from missing keys.
- **Malformed JSON lines are never fatal.** Count them, continue, surface the count.
- **Generated output never lands in the repo.** `stats.json`, `dashboard.html` and `cache.json` go to `~/.cache/claude-acs-stats/` (overridable with `--out`). Only source lives in the repo.
- **Repo location override order:** `$CLAUDE_ACS_REPO` → `$CLAUDE_ACS_PROFILES_REPO` → `$HOME/repos/acs-agentic-setup`.
- **Short keys (`a`,`d`,`h`,`p`,`n`,`x`,`m`,`in`,`out`,`cr`,`cw`) are used *only* inside `prompt_buckets` and `token_buckets`.** Every other object uses readable key names.
- Run tests from the `scripts/` directory: `cd scripts && python3 -m unittest discover -s . -v`. There is no `scripts/__init__.py`, so the dotted `scripts.test_x` form does not work.
- **Target Python 3.9.6** (`/usr/bin/python3` on this machine). No PEP 585 builtin generics (`list[str]`) or `X | None` unions in code — they raise at import on 3.9. Annotations in the Interfaces blocks below are documentation, not code.

---

## File Structure

All paths relative to `setups/claude-code-usage-stats/`.

| File | Responsibility |
|---|---|
| `scripts/discovery.py` | Find accounts on disk. Nothing else. |
| `scripts/history.py` | Parse `history.jsonl`; compute total/exclusive/shared; build prompt buckets. |
| `scripts/transcripts.py` | Roll up one transcript file: turns, per-model tokens, tools, branches, versions. |
| `scripts/cache.py` | Load/save the incremental parse cache; decide hit vs miss. |
| `scripts/aggregate.py` | Assemble the `stats.json` dict from the layers. Owns the schema. |
| `scripts/render.py` | Inject a JSON blob into `dashboard/template.html`. |
| `scripts/collect_stats.py` | CLI: argument parsing, orchestration, writing artifacts. |
| `scripts/test_*.py` | One test module per unit above. |
| `dashboard/template.html` | The dashboard. Panels, account filter, raw/deduped toggle. |
| `README.md`, `INDEX.md` | Setup docs, matching the sibling setup's conventions. |

Modules are siblings, imported by plain `import discovery` after `collect_stats.py`
puts its own directory on `sys.path`. This keeps invocation identical to
`apply_profile.py` (`python3 <path-to-script>`), with no package install step.

---

### Task 1: Account discovery

**Files:**
- Create: `scripts/discovery.py`
- Test: `scripts/test_discovery.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Account` namedtuple with fields `(name, path, is_canonical, history_path, projects_dir)` where `path`/`history_path`/`projects_dir` are `pathlib.Path` and `history_path`/`projects_dir` may be `None`; and `discover_accounts(store: Path, canonical: Path) -> list[Account]`, sorted by `name`, canonical last.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_discovery.py
import tempfile, unittest
from pathlib import Path
import discovery


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.canonical = self.tmp / "canonical"

    def _mk(self, path, history=True, projects=True):
        path.mkdir(parents=True, exist_ok=True)
        if history:
            (path / "history.jsonl").write_text("")
        if projects:
            (path / "projects").mkdir(exist_ok=True)

    def test_finds_accounts_and_excludes_bin(self):
        self._mk(self.store / "harrison")
        self._mk(self.store / "personal")
        # bin/ lives inside the store but is not an account
        (self.store / "bin").mkdir(parents=True)
        (self.store / "bin" / "some-script.sh").write_text("#!/bin/sh\n")
        self._mk(self.canonical)

        accounts = discovery.discover_accounts(self.store, self.canonical)
        names = [a.name for a in accounts]

        self.assertEqual(names, ["harrison", "personal", "canonical"])
        self.assertTrue(accounts[-1].is_canonical)
        self.assertFalse(accounts[0].is_canonical)

    def test_history_only_account_is_valid(self):
        self._mk(self.store / "personal2", history=True, projects=False)
        accounts = discovery.discover_accounts(self.store, self.canonical)
        self.assertEqual([a.name for a in accounts], ["personal2"])
        self.assertIsNone(accounts[0].projects_dir)
        self.assertIsNotNone(accounts[0].history_path)

    def test_projects_only_account_is_valid(self):
        self._mk(self.store / "fresh", history=False, projects=True)
        accounts = discovery.discover_accounts(self.store, self.canonical)
        self.assertEqual([a.name for a in accounts], ["fresh"])
        self.assertIsNone(accounts[0].history_path)

    def test_missing_store_returns_empty(self):
        self.assertEqual(discovery.discover_accounts(self.tmp / "nope", self.tmp / "nope2"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_discovery -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'discovery'`

Note: run tests from the `scripts/` directory (`cd scripts && python3 -m unittest test_discovery -v`) so sibling imports resolve, or export `PYTHONPATH=scripts`. Pick one and use it consistently for every task.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/discovery.py
"""Locate Claude Code account config directories on disk."""
from collections import namedtuple
from pathlib import Path

Account = namedtuple("Account", "name path is_canonical history_path projects_dir")

# Directories that live inside the account store but are not accounts.
_NOT_ACCOUNTS = {"bin"}


def _build(name, path, is_canonical):
    """Return an Account if `path` looks like an account dir, else None."""
    history = path / "history.jsonl"
    projects = path / "projects"
    has_history = history.is_file()
    has_projects = projects.is_dir()
    if not (has_history or has_projects):
        return None
    return Account(
        name=name,
        path=path,
        is_canonical=is_canonical,
        history_path=history if has_history else None,
        projects_dir=projects if has_projects else None,
    )


def discover_accounts(store, canonical):
    """Accounts under `store`, sorted by name, with `canonical` appended last.

    A directory counts as an account only if it holds history.jsonl or a
    projects/ dir -- this is what keeps `store/bin` from registering.
    """
    store, canonical = Path(store), Path(canonical)
    found = []
    if store.is_dir():
        for child in sorted(store.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name in _NOT_ACCOUNTS or child.name.startswith("."):
                continue
            account = _build(child.name, child, False)
            if account:
                found.append(account)
    canonical_account = _build("canonical", canonical, True)
    if canonical_account:
        found.append(canonical_account)
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_discovery -v`
Expected: 4 tests PASS

- [ ] **Step 5: Sanity-check against the real machine**

Run:
```bash
cd scripts && python3 -c "
import discovery, os
from pathlib import Path
for a in discovery.discover_accounts(Path.home()/'.claude-accounts', Path.home()/'.claude'):
    print(a.name, a.is_canonical, bool(a.history_path), bool(a.projects_dir))
"
```
Expected: `harrison`, `personal`, `personal2`, then `canonical` — and **no `bin`**.

- [ ] **Step 6: Commit**

```bash
git add scripts/discovery.py scripts/test_discovery.py
git commit -m "feat(usage-stats): account discovery with bin/ exclusion"
```

---

### Task 2: History parsing, dedup, and prompt buckets

**Files:**
- Create: `scripts/history.py`
- Test: `scripts/test_history.py`

**Interfaces:**
- Consumes: `discovery.Account` from Task 1.
- Produces:
  - `read_history(path) -> tuple[list[dict], int]` — returns `(entries, malformed_count)`; each entry is `{"key": (ts_ms, display), "ts_ms": int, "project": str, "session_id": str, "chars": int, "pasted": bool, "slash": str|None}`.
  - `dedup_figures(per_account: dict[str, list[dict]]) -> dict` — returns `{"per_account": {name: {"total": int, "exclusive": int, "shared": int}}, "union": int, "sum_of_totals": int}`.
  - `bucket_prompts(per_account, exclusive_keys) -> list[dict]` — rows `{"a","d","h","p","n","x"}`, UTC, sorted by `(a, d, h, p)`.
  - `char_stats(entries) -> dict` — `{"mean","median","p90","p99","max"}` (ints; `{}` when no entries).

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_history.py
import json, tempfile, unittest
from pathlib import Path
import history

# 2026-08-11T04:02:11Z -- 14:02 the SAME day in Australia/Sydney.
# Paired with TS_B below, which DOES cross the date line, so a local-time bug
# shows up as a row in the wrong date bucket.
TS_A = 1786420931000
# 2026-08-11T23:30:00Z -- 09:30 next day in Sydney.
TS_B = 1786491000000


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def row(ts, display, project="~/repos/x", session="s1"):
    return {"display": display, "timestamp": ts, "project": project,
            "sessionId": session, "pastedContents": {}}


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_malformed_lines_counted_not_fatal(self):
        p = self.tmp / "history.jsonl"
        p.write_text(json.dumps(row(TS_A, "one")) + "\n{ broken\n" + json.dumps(row(TS_B, "two")) + "\n\n")
        entries, malformed = history.read_history(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(malformed, 1)  # blank lines are skipped, not counted

    def test_slash_and_chars_extracted(self):
        p = self.tmp / "history.jsonl"
        write(p, [row(TS_A, "/code-review please"), row(TS_B, "plain prompt")])
        entries, _ = history.read_history(p)
        self.assertEqual(entries[0]["slash"], "/code-review")
        self.assertIsNone(entries[1]["slash"])
        self.assertEqual(entries[0]["chars"], len("/code-review please"))

    def test_three_figures_and_shared_identity(self):
        shared = [row(TS_A, "shared-one"), row(TS_B, "shared-two")]
        a = shared + [row(TS_A, "a-only")]
        b = shared + [row(TS_A, "b-only-1"), row(TS_B, "b-only-2")]
        pa, _ = history.read_history_rows(a)
        pb, _ = history.read_history_rows(b)

        fig = history.dedup_figures({"a": pa, "b": pb})

        self.assertEqual(fig["per_account"]["a"], {"total": 3, "exclusive": 1, "shared": 2})
        self.assertEqual(fig["per_account"]["b"], {"total": 4, "exclusive": 2, "shared": 2})
        self.assertEqual(fig["union"], 5)          # 2 shared + 1 + 2
        self.assertEqual(fig["sum_of_totals"], 7)
        for name, f in fig["per_account"].items():
            self.assertEqual(f["shared"], f["total"] - f["exclusive"], name)
        self.assertLessEqual(fig["union"], fig["sum_of_totals"])

    def test_figures_invariant_under_account_order(self):
        pa, _ = history.read_history_rows([row(TS_A, "s"), row(TS_A, "a1")])
        pb, _ = history.read_history_rows([row(TS_A, "s"), row(TS_B, "b1")])
        one = history.dedup_figures({"a": pa, "b": pb})
        two = history.dedup_figures({"b": pb, "a": pa})
        self.assertEqual(one, two)

    def test_buckets_are_utc_and_lossless(self):
        pa, _ = history.read_history_rows([
            row(TS_A, "p1", project="~/repos/x"),
            row(TS_A, "p2", project="~/repos/x"),
            row(TS_B, "p3", project="~/repos/y"),
        ])
        fig = history.dedup_figures({"a": pa})
        rows = history.bucket_prompts({"a": pa}, fig["exclusive_keys"])

        by_key = {(r["d"], r["h"], r["p"]): r["n"] for r in rows}
        # TS_A is 04:00 UTC on 2026-08-11 -- NOT 14:00 on the same local day
        self.assertEqual(by_key[("2026-08-11", 4, "~/repos/x")], 2)
        # TS_B is 23:00 UTC on 2026-08-11, which is the NEXT day in Sydney
        self.assertEqual(by_key[("2026-08-11", 23, "~/repos/y")], 1)
        self.assertEqual(sum(r["n"] for r in rows), 3)
        self.assertLessEqual(len(rows), 3)

    def test_char_stats(self):
        entries = [{"chars": c} for c in [10, 20, 30, 40, 100]]
        st = history.char_stats(entries)
        self.assertEqual(st["max"], 100)
        self.assertEqual(st["median"], 30)
        self.assertEqual(history.char_stats([]), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_history -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/history.py
"""Layer A: parse history.jsonl, compute dedup figures, build prompt buckets."""
import json
import statistics
from datetime import datetime, timezone


def _entry(raw):
    """Normalise one history line into the internal entry shape, or None."""
    ts = raw.get("timestamp")
    display = raw.get("display")
    if not isinstance(ts, (int, float)) or not isinstance(display, str):
        return None
    stripped = display.lstrip()
    slash = stripped.split()[0] if stripped.startswith("/") and stripped.split() else None
    return {
        "key": (int(ts), display),
        "ts_ms": int(ts),
        "project": raw.get("project") or "(unknown)",
        "session_id": raw.get("sessionId") or "(none)",
        "chars": len(display),
        "pasted": bool(raw.get("pastedContents")),
        "slash": slash,
    }


def read_history_rows(rows):
    """Normalise already-parsed dicts. Returns (entries, malformed_count)."""
    entries, malformed = [], 0
    for raw in rows:
        e = _entry(raw)
        if e is None:
            malformed += 1
        else:
            entries.append(e)
    return entries, malformed


def read_history(path):
    """Parse a history.jsonl file. Returns (entries, malformed_count).

    Blank lines are skipped silently; unparseable or malformed lines are counted.
    """
    entries, malformed = [], 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            e = _entry(raw)
            if e is None:
                malformed += 1
            else:
                entries.append(e)
    return entries, malformed


def dedup_figures(per_account):
    """Set-theoretic total/exclusive/shared per account, plus global figures.

    Order-independent by construction: exclusive is a pure set difference and
    union is a cardinality, so no first-seen tie-break exists or is needed.
    Also returns `exclusive_keys` for bucket construction.
    """
    keysets = {name: {e["key"] for e in entries} for name, entries in per_account.items()}
    figures, exclusive_keys = {}, {}
    for name, keys in keysets.items():
        others = set()
        for other, other_keys in keysets.items():
            if other != name:
                others |= other_keys
        exclusive = keys - others
        exclusive_keys[name] = exclusive
        total = len(per_account[name])
        figures[name] = {
            "total": total,
            "exclusive": len(exclusive),
            "shared": total - len(exclusive),
        }
    union = set()
    for keys in keysets.values():
        union |= keys
    return {
        "per_account": figures,
        "union": len(union),
        "sum_of_totals": sum(len(v) for v in per_account.values()),
        "exclusive_keys": exclusive_keys,
    }


def bucket_prompts(per_account, exclusive_keys):
    """One row per (account, UTC date, UTC hour, project). Lossless in `n`."""
    acc = {}
    for name, entries in per_account.items():
        exclusive = exclusive_keys.get(name, set())
        for e in entries:
            dt = datetime.fromtimestamp(e["ts_ms"] / 1000, tz=timezone.utc)
            k = (name, dt.strftime("%Y-%m-%d"), dt.hour, e["project"])
            cell = acc.setdefault(k, [0, 0])
            cell[0] += 1
            if e["key"] in exclusive:
                cell[1] += 1
    return [
        {"a": a, "d": d, "h": h, "p": p, "n": n, "x": x}
        for (a, d, h, p), (n, x) in sorted(acc.items())
    ]


def char_stats(entries):
    """Prompt-length aggregates. No per-prompt value is ever emitted."""
    lengths = sorted(e["chars"] for e in entries)
    if not lengths:
        return {}

    def pct(p):
        return lengths[int(p / 100 * (len(lengths) - 1))]

    return {
        "mean": int(statistics.mean(lengths)),
        "median": int(statistics.median(lengths)),
        "p90": pct(90),
        "p99": pct(99),
        "max": lengths[-1],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_history -v`
Expected: 6 tests PASS

- [ ] **Step 5: Verify against real data — the figures must match the known values**

Run:
```bash
cd scripts && python3 -c "
import history
from pathlib import Path
store = Path.home()/'.claude-accounts'
per = {}
for n in ['harrison','personal','personal2']:
    per[n], _ = history.read_history(store/n/'history.jsonl')
per['canonical'], _ = history.read_history(Path.home()/'.claude'/'history.jsonl')
f = history.dedup_figures(per)
for n, v in sorted(f['per_account'].items()): print(n, v)
print('union', f['union'], 'sum', f['sum_of_totals'])
"
```
Expected **as of the 2026-08-21T04:12Z snapshot** this design was measured against: `harrison` total 7527 / exclusive 4594, `personal` total 3500 / exclusive 567, `personal2` total 2331 / exclusive 1, `canonical` total 2237 / exclusive 137, **union 8232**.

**These are a dated snapshot, not an invariant.** `history.jsonl` grows while you work — running this very plan appends to it — so a later run will legitimately exceed them. Exceeding is not a regression.

What *is* invariant, and what to check instead: **`shared` must be exactly 2933 / 2933 / 2330 / 2100.** The shared prefix is frozen (see the spec's "The shared prefix is frozen"), so those four numbers cannot change. Growth adds only *exclusive* rows. If `shared` drifts, the `(timestamp, display)` key is wrong — stop and fix. To reproduce the union figure exactly, filter with `--since`/`--until` to the snapshot instant.

- [ ] **Step 6: Commit**

```bash
git add scripts/history.py scripts/test_history.py
git commit -m "feat(usage-stats): history parsing, three-figure dedup, UTC prompt buckets"
```

---

### Task 3: Transcript rollup

**Files:**
- Create: `scripts/transcripts.py`
- Test: `scripts/test_transcripts.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure per-file function).
- Produces: `rollup_file(path) -> dict` with shape:
  `{"schema": 1, "turns": int, "sidechain_turns": int, "user_turns": int, "malformed": int, "first_ts": str|None, "last_ts": str|None, "sessions": {session_id: {...}}, "by_day_model": {(date, model): {...}} serialised as {"YYYY-MM-DD|model": {...}}, "tools": {name: int}, "branches": {name: int}, "versions": {name: int}, "projects": {cwd: int}}`
  where each token dict is `{"in","out","cr","cw","turns","sidechain_turns"}`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_transcripts.py
import json, tempfile, unittest
from pathlib import Path
import transcripts

TS1 = "2026-08-11T04:02:11.000Z"
TS2 = "2026-08-11T06:40:02.000Z"
TS3 = "2026-08-13T01:00:00.000Z"


def usage(i, o, cr, cw):
    """A usage block shaped like the real thing -- including the iterations[]
    array that repeats the same numbers and MUST NOT be double-counted."""
    return {
        "input_tokens": i, "output_tokens": o,
        "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
        "iterations": [{"input_tokens": i, "output_tokens": o,
                        "cache_read_input_tokens": cr,
                        "cache_creation_input_tokens": cw}],
    }


def assistant(mid, model="claude-sonnet-5", ts=TS1, u=None, tools=(), sidechain=False):
    content = [{"type": "tool_use", "name": t} for t in tools]
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1",
            "isSidechain": sidechain, "cwd": "/repo", "gitBranch": "main",
            "version": "2.1.220",
            "message": {"id": mid, "model": model, "content": content,
                        "usage": u or usage(2, 40, 1000, 10)}}


def write(path, rows):
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestTranscripts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "session.jsonl"

    def test_iterations_not_double_counted(self):
        write(self.p, [assistant("m1", u=usage(10, 20, 30, 40))])
        r = transcripts.rollup_file(self.p)
        tok = list(r["by_day_model"].values())[0]
        self.assertEqual((tok["in"], tok["out"], tok["cr"], tok["cw"]), (10, 20, 30, 40))

    def test_repeated_message_id_counted_once(self):
        write(self.p, [assistant("m1", u=usage(10, 20, 30, 40)),
                       assistant("m1", u=usage(10, 20, 30, 40)),
                       assistant("m2", u=usage(1, 2, 3, 4))])
        r = transcripts.rollup_file(self.p)
        tok = list(r["by_day_model"].values())[0]
        self.assertEqual(tok["in"], 11)   # 10 + 1, not 21
        self.assertEqual(tok["out"], 22)

    def test_tools_branches_versions_counted(self):
        write(self.p, [assistant("m1", tools=("Bash", "Read", "Bash"))])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["tools"], {"Bash": 2, "Read": 1})
        self.assertEqual(r["branches"], {"main": 1})
        self.assertEqual(r["versions"], {"2.1.220": 1})

    def test_sidechain_split_out(self):
        write(self.p, [assistant("m1"), assistant("m2", sidechain=True)])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["sidechain_turns"], 1)

    def test_session_span_and_resumed_flag(self):
        write(self.p, [assistant("m1", ts=TS1), assistant("m2", ts=TS3)])
        r = transcripts.rollup_file(self.p)
        s = r["sessions"]["s1"]
        self.assertEqual(s["first_ts"], "2026-08-11T04:02:11Z")
        self.assertEqual(s["last_ts"], "2026-08-13T01:00:00Z")
        self.assertTrue(s["resumed"])          # spans >1 calendar day
        self.assertNotIn("duration", s)

    def test_single_day_session_not_resumed(self):
        write(self.p, [assistant("m1", ts=TS1), assistant("m2", ts=TS2)])
        r = transcripts.rollup_file(self.p)
        self.assertFalse(r["sessions"]["s1"]["resumed"])

    def test_malformed_lines_counted_not_fatal(self):
        self.p.write_text(json.dumps(assistant("m1")) + "\n{ nope\n")
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["malformed"], 1)
        self.assertEqual(r["turns"], 1)

    def test_missing_usage_is_safe(self):
        write(self.p, [{"type": "user", "timestamp": TS1, "sessionId": "s1",
                        "message": {"content": "hi"}}])
        r = transcripts.rollup_file(self.p)
        self.assertEqual(r["user_turns"], 1)
        self.assertEqual(r["by_day_model"], {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_transcripts -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'transcripts'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/transcripts.py
"""Layer B: roll up one session transcript file into token/tool/session counts."""
import json
from datetime import datetime, timezone

ROLLUP_SCHEMA = 1
_TOKEN_FIELDS = (
    ("in", "input_tokens"),
    ("out", "output_tokens"),
    ("cr", "cache_read_input_tokens"),
    ("cw", "cache_creation_input_tokens"),
)


def _iso_utc(raw):
    """Normalise a transcript timestamp to 'YYYY-MM-DDTHH:MM:SSZ', or None."""
    if not isinstance(raw, str):
        return None
    text = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _blank_tokens():
    return {"in": 0, "out": 0, "cr": 0, "cw": 0, "turns": 0, "sidechain_turns": 0}


def rollup_file(path):
    """Aggregate a single transcript. Never raises on bad content."""
    out = {
        "schema": ROLLUP_SCHEMA, "turns": 0, "sidechain_turns": 0, "user_turns": 0,
        "malformed": 0, "first_ts": None, "last_ts": None,
        "sessions": {}, "by_day_model": {}, "tools": {}, "branches": {},
        "versions": {}, "projects": {},
    }
    seen_message_ids = set()

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                out["malformed"] += 1
                continue
            if not isinstance(rec, dict):
                out["malformed"] += 1
                continue

            rtype = rec.get("type")
            ts = _iso_utc(rec.get("timestamp"))
            if ts:
                if out["first_ts"] is None or ts < out["first_ts"]:
                    out["first_ts"] = ts
                if out["last_ts"] is None or ts > out["last_ts"]:
                    out["last_ts"] = ts

            sid = rec.get("sessionId")
            if sid and ts:
                sess = out["sessions"].setdefault(sid, {
                    "first_ts": ts, "last_ts": ts, "turns": 0, "sidechain_turns": 0,
                    "user_turns": 0, "models": [], "branches": [], "project": rec.get("cwd"),
                    **{k: 0 for k, _ in _TOKEN_FIELDS},
                })
                sess["first_ts"] = min(sess["first_ts"], ts)
                sess["last_ts"] = max(sess["last_ts"], ts)
            else:
                sess = None

            if rtype == "user":
                out["user_turns"] += 1
                if sess:
                    sess["user_turns"] += 1
                continue
            if rtype != "assistant":
                continue

            sidechain = bool(rec.get("isSidechain"))
            out["turns"] += 1
            if sidechain:
                out["sidechain_turns"] += 1
            if sess:
                sess["turns"] += 1
                if sidechain:
                    sess["sidechain_turns"] += 1

            for field, key in (("gitBranch", "branches"), ("version", "versions"), ("cwd", "projects")):
                val = rec.get(field)
                if val:
                    out[key][val] = out[key].get(val, 0) + 1
            if sess and rec.get("gitBranch") and rec["gitBranch"] not in sess["branches"]:
                sess["branches"].append(rec["gitBranch"])

            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue

            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name") or "(unnamed)"
                        out["tools"][name] = out["tools"].get(name, 0) + 1

            model = msg.get("model")
            usage = msg.get("usage")
            if not isinstance(usage, dict) or not model:
                continue

            # Trap: resumed/retried turns re-emit the same message id.
            mid = msg.get("id")
            if mid is not None:
                if mid in seen_message_ids:
                    continue
                seen_message_ids.add(mid)

            day = (ts or "0000-00-00T00:00:00Z")[:10]
            cell = out["by_day_model"].setdefault(f"{day}|{model}", _blank_tokens())
            cell["turns"] += 1
            if sidechain:
                cell["sidechain_turns"] += 1
            # Trap: usage carries an iterations[] array repeating these same
            # figures. Read ONLY the top-level fields.
            for short, long in _TOKEN_FIELDS:
                val = usage.get(long) or 0
                if isinstance(val, (int, float)):
                    cell[short] += int(val)
                    if sess:
                        sess[short] += int(val)
            if sess and model not in sess["models"]:
                sess["models"].append(model)

    for sess in out["sessions"].values():
        sess["resumed"] = sess["first_ts"][:10] != sess["last_ts"][:10]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_transcripts -v`
Expected: 8 tests PASS

- [ ] **Step 5: Smoke-test on one real transcript**

Run:
```bash
cd scripts && python3 -c "
import transcripts, glob, json
from pathlib import Path
f = sorted(glob.glob(str(Path.home()/'.claude-accounts/harrison/projects/**/*.jsonl'), recursive=True), key=lambda p: -Path(p).stat().st_size)[0]
r = transcripts.rollup_file(f)
print('file', Path(f).name, 'turns', r['turns'], 'sidechain', r['sidechain_turns'], 'malformed', r['malformed'])
print('models/days', list(r['by_day_model'])[:3])
print('top tools', sorted(r['tools'].items(), key=lambda kv: -kv[1])[:5])
"
```
Expected: non-zero turns, plausible tool names, `malformed 0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/transcripts.py scripts/test_transcripts.py
git commit -m "feat(usage-stats): transcript rollup with message.id and iterations[] guards"
```

---

### Task 4: Incremental parse cache

**Files:**
- Create: `scripts/cache.py`
- Test: `scripts/test_cache.py`

**Interfaces:**
- Consumes: `transcripts.ROLLUP_SCHEMA` from Task 3.
- Produces:
  - `Cache(path, rollup_schema)` with `.get(file_path) -> dict|None`, `.put(file_path, rollup) -> None`, `.save() -> None`, and counters `.hits: int`, `.misses: int`.
  - `Cache.load(path, rollup_schema) -> Cache` classmethod; a schema mismatch or unreadable file yields an empty cache rather than an error.

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_cache.py
import json, tempfile, time, unittest
from pathlib import Path
import cache


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cache_path = self.tmp / "cache.json"
        self.data = self.tmp / "f.jsonl"
        self.data.write_text("one\n")

    def test_miss_then_hit(self):
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.data))
        c.put(self.data, {"turns": 5})
        c.save()

        c2 = cache.Cache.load(self.cache_path, 1)
        self.assertEqual(c2.get(self.data), {"turns": 5})
        self.assertEqual(c2.hits, 1)

    def test_size_change_invalidates(self):
        c = cache.Cache.load(self.cache_path, 1)
        c.put(self.data, {"turns": 5})
        c.save()

        self.data.write_text("one\ntwo\n")        # append -> size changes
        c2 = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c2.get(self.data))
        self.assertEqual(c2.misses, 1)

    def test_schema_bump_discards_everything(self):
        c = cache.Cache.load(self.cache_path, 1)
        c.put(self.data, {"turns": 5})
        c.save()

        c2 = cache.Cache.load(self.cache_path, 2)
        self.assertIsNone(c2.get(self.data))

    def test_corrupt_cache_file_is_survivable(self):
        self.cache_path.write_text("{ not json")
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.data))
        c.put(self.data, {"turns": 1})
        c.save()
        self.assertEqual(cache.Cache.load(self.cache_path, 1).get(self.data), {"turns": 1})

    def test_missing_file_is_a_miss_not_a_crash(self):
        c = cache.Cache.load(self.cache_path, 1)
        self.assertIsNone(c.get(self.tmp / "does-not-exist.jsonl"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_cache -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/cache.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_cache -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/cache.py scripts/test_cache.py
git commit -m "feat(usage-stats): incremental parse cache with self-invalidating keys"
```

---

### Task 5: Aggregation into the schema

**Files:**
- Create: `scripts/aggregate.py`
- Test: `scripts/test_aggregate.py`

**Interfaces:**
- Consumes: `discovery.Account`, `history.read_history`/`dedup_figures`/`bucket_prompts`/`char_stats`, `transcripts.rollup_file`, `cache.Cache`.
- Note: `<synthetic>` appears as a real `message.model` value carrying all-zero usage, so it creates a legitimate `token_buckets` row with no tokens. Keep it in the data; the dashboard filters it from model-mix charts.
- Produces: `build_stats(accounts, *, store_label, since=None, until=None, cache=None, timezone_name=None, on_progress=None) -> dict` — the complete `stats.json` dict, `schema_version` 1.
- Also produces `SCHEMA_VERSION = 1` and `merge_rollups(rollups) -> dict` (pure helper, exercised directly by tests).

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_aggregate.py
import json, tempfile, unittest
from pathlib import Path
import aggregate, discovery

TS = 1786507331000   # 2026-08-11T04:02:11Z


def hrow(ts, display, project="~/repos/x", session="s1"):
    return {"display": display, "timestamp": ts, "project": project,
            "sessionId": session, "pastedContents": {}}


def arow(mid, ts="2026-08-11T04:02:11.000Z", model="claude-sonnet-5"):
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1",
            "isSidechain": False, "cwd": "/repo", "gitBranch": "main",
            "version": "2.1.220",
            "message": {"id": mid, "model": model, "content": [{"type": "tool_use", "name": "Bash"}],
                        "usage": {"input_tokens": 5, "output_tokens": 7,
                                  "cache_read_input_tokens": 9, "cache_creation_input_tokens": 3}}}


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.canonical = self.tmp / "canonical"

        # account "a": 2 prompts (1 shared with b), 1 transcript
        a = self.store / "a"
        (a / "projects" / "proj").mkdir(parents=True)
        (a / "history.jsonl").write_text(
            json.dumps(hrow(TS, "shared")) + "\n" + json.dumps(hrow(TS, "a-only")) + "\n")
        (a / "projects" / "proj" / "s1.jsonl").write_text(
            json.dumps(arow("m1")) + "\n" + json.dumps(arow("m1")) + "\n")   # dup id

        # account "b": duplicate-only history, but a UNIQUE transcript
        b = self.store / "b"
        (b / "projects" / "proj").mkdir(parents=True)
        (b / "history.jsonl").write_text(json.dumps(hrow(TS, "shared")) + "\n")
        (b / "projects" / "proj" / "s2.jsonl").write_text(json.dumps(arow("m9")) + "\n")

        self.accounts = discovery.discover_accounts(self.store, self.canonical)

    def build(self):
        return aggregate.build_stats(self.accounts, store_label=str(self.store),
                                     timezone_name="Australia/Sydney")

    def test_schema_version_and_utc_marker(self):
        s = self.build()
        self.assertEqual(s["schema_version"], 1)
        self.assertEqual(s["timezone"], "Australia/Sydney")
        self.assertTrue(s["generated_at"].endswith("Z"))

    def test_three_figures_present_and_consistent(self):
        s = self.build()
        by = {a["name"]: a for a in s["accounts"]}
        self.assertEqual(by["a"]["prompts"], {"total": 2, "exclusive": 1, "shared": 1})
        self.assertEqual(by["b"]["prompts"], {"total": 1, "exclusive": 0, "shared": 1})
        self.assertEqual(s["totals"]["union"], 2)
        self.assertEqual(s["totals"]["sum_of_totals"], 3)

    def test_prompt_buckets_lossless(self):
        s = self.build()
        self.assertEqual(sum(r["n"] for r in s["prompt_buckets"]), s["totals"]["sum_of_totals"])
        self.assertLessEqual(len(s["prompt_buckets"]), s["totals"]["sum_of_totals"])

    def test_duplicate_history_account_still_contributes_tokens(self):
        """The whole point of 'dead is per-layer': b's history is a duplicate,
        but its transcript tokens are unique and must appear."""
        s = self.build()
        b_tokens = [t for t in s["token_buckets"] if t["a"] == "b"]
        self.assertEqual(len(b_tokens), 1)
        self.assertEqual(b_tokens[0]["in"], 5)

    def test_message_id_dedup_survives_aggregation(self):
        s = self.build()
        a_tokens = [t for t in s["token_buckets"] if t["a"] == "a"][0]
        self.assertEqual(a_tokens["in"], 5)     # not 10

    def test_no_duration_key_anywhere(self):
        blob = json.dumps(self.build())
        self.assertNotIn("duration", blob.replace('"duration_seconds"', ""))

    def test_histogram_agrees_with_session_rows(self):
        s = self.build()
        for acct, hist in s["session_histogram"].items():
            rows = [r for r in s["sessions"] if r["a"] == acct]
            rebuilt = {}
            for r in rows:
                k = str(r["prompts"])
                rebuilt[k] = rebuilt.get(k, 0) + 1
            self.assertEqual(hist, rebuilt, acct)

    def test_projects_rollup_matches_buckets(self):
        s = self.build()
        from collections import defaultdict
        derived = defaultdict(int)
        for r in s["prompt_buckets"]:
            derived[(r["a"], r["p"])] += r["n"]
        for row in s["projects"]:
            self.assertEqual(row["n"], derived[(row["a"], row["path"])])

    def test_no_per_prompt_row_emitted(self):
        s = self.build()
        for key in ("prompts_raw", "prompt_rows", "prompt_hashes"):
            self.assertNotIn(key, s)
        self.assertNotIn("a-only", json.dumps(s))   # no prompt text leaks

    def test_run_block_reports_counters(self):
        s = self.build()
        self.assertIn("files_scanned", s["run"])
        self.assertIn("malformed_lines", s["run"])
        self.assertEqual(s["run"]["accounts_skipped"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_aggregate -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aggregate'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/aggregate.py
"""Assemble stats.json. This module owns the schema."""
import time
from collections import defaultdict
from datetime import datetime, timezone

import cache as cache_mod
import history
import transcripts

SCHEMA_VERSION = 1


def _sorted_counts(mapping):
    """[{'a':acct,'name':k,'n':v}] sorted by count desc then name."""
    return [
        {"a": acct, "name": name, "n": n}
        for acct, name, n in sorted(
            ((acct, name, n) for acct, inner in mapping.items() for name, n in inner.items()),
            key=lambda t: (t[0], -t[2], t[1]),
        )
    ]


def merge_rollups(rollups):
    """Combine per-file rollups into one account-level rollup."""
    out = {
        "turns": 0, "sidechain_turns": 0, "user_turns": 0, "malformed": 0,
        "by_day_model": {}, "tools": {}, "branches": {}, "versions": {}, "sessions": {},
    }
    for r in rollups:
        for k in ("turns", "sidechain_turns", "user_turns", "malformed"):
            out[k] += r.get(k, 0)
        for k in ("tools", "branches", "versions"):
            for name, n in (r.get(k) or {}).items():
                out[k][name] = out[k].get(name, 0) + n
        for key, cell in (r.get("by_day_model") or {}).items():
            dst = out["by_day_model"].setdefault(
                key, {"in": 0, "out": 0, "cr": 0, "cw": 0, "turns": 0, "sidechain_turns": 0})
            for f in dst:
                dst[f] += cell.get(f, 0)
        for sid, s in (r.get("sessions") or {}).items():
            dst = out["sessions"].get(sid)
            if dst is None:
                out["sessions"][sid] = dict(s)
                continue
            dst["first_ts"] = min(dst["first_ts"], s["first_ts"])
            dst["last_ts"] = max(dst["last_ts"], s["last_ts"])
            for f in ("turns", "sidechain_turns", "user_turns", "in", "out", "cr", "cw"):
                dst[f] = dst.get(f, 0) + s.get(f, 0)
            for f in ("models", "branches"):
                for v in s.get(f) or []:
                    if v not in dst.setdefault(f, []):
                        dst[f].append(v)
            dst["project"] = dst.get("project") or s.get("project")
            dst["resumed"] = dst["first_ts"][:10] != dst["last_ts"][:10]
    return out


def build_stats(accounts, *, store_label, since=None, until=None,
                cache=None, timezone_name=None, on_progress=None):
    started = time.time()
    run = {"files_scanned": 0, "files_parsed": 0, "cache_hits": 0,
           "malformed_lines": 0, "accounts_skipped": []}

    # ---- Layer A ------------------------------------------------------------
    per_account_entries, char_by_account, pasted_by_account = {}, {}, {}
    for acct in accounts:
        if acct.history_path is None:
            run["accounts_skipped"].append({"name": acct.name, "reason": "no history.jsonl"})
            per_account_entries[acct.name] = []
            continue
        entries, malformed = history.read_history(acct.history_path)
        run["malformed_lines"] += malformed
        if since or until:
            entries = [e for e in entries if _in_window(e["ts_ms"], since, until)]
        per_account_entries[acct.name] = entries
        char_by_account[acct.name] = history.char_stats(entries)
        pasted_by_account[acct.name] = sum(1 for e in entries if e["pasted"])

    figures = history.dedup_figures(per_account_entries)
    prompt_buckets = history.bucket_prompts(per_account_entries, figures["exclusive_keys"])

    # ---- Layer B ------------------------------------------------------------
    merged_by_account, prompts_per_session = {}, defaultdict(lambda: defaultdict(int))
    for acct in accounts:
        for e in per_account_entries[acct.name]:
            prompts_per_session[acct.name][e["session_id"]] += 1

        rollups = []
        if acct.projects_dir:
            for path in sorted(acct.projects_dir.rglob("*.jsonl")):
                run["files_scanned"] += 1
                hit = cache.get(path) if cache else None
                if hit is None:
                    hit = transcripts.rollup_file(path)
                    run["files_parsed"] += 1
                    if cache:
                        cache.put(path, hit)
                rollups.append(hit)
                run["malformed_lines"] += hit.get("malformed", 0)
                if on_progress:
                    on_progress(run["files_scanned"])
        merged_by_account[acct.name] = merge_rollups(rollups)
    if cache:
        run["cache_hits"] = cache.hits

    # ---- Assemble -----------------------------------------------------------
    token_buckets = []
    for name, merged in sorted(merged_by_account.items()):
        for key, cell in sorted(merged["by_day_model"].items()):
            day, model = key.split("|", 1)
            if not _in_day_window(day, since, until):
                continue
            token_buckets.append({"a": name, "d": day, "m": model, **cell})

    sessions = []
    for name, merged in sorted(merged_by_account.items()):
        for sid, s in sorted(merged["sessions"].items()):
            sessions.append({
                "a": name, "id": sid, "p": s.get("project"),
                "prompts": prompts_per_session[name].get(sid, 0),
                "turns": s.get("turns", 0), "sidechain_turns": s.get("sidechain_turns", 0),
                "first_ts": s["first_ts"], "last_ts": s["last_ts"],
                "resumed": bool(s.get("resumed")),
                "models": sorted(s.get("models") or []),
                "branches": sorted(s.get("branches") or []),
                **{f: s.get(f, 0) for f in ("in", "out", "cr", "cw")},
            })

    histogram = defaultdict(lambda: defaultdict(int))
    for row in sessions:
        histogram[row["a"]][str(row["prompts"])] += 1

    project_rows = defaultdict(lambda: {"n": 0, "first": None, "last": None})
    for r in prompt_buckets:
        cell = project_rows[(r["a"], r["p"])]
        cell["n"] += r["n"]
        cell["first"] = r["d"] if cell["first"] is None else min(cell["first"], r["d"])
        cell["last"] = r["d"] if cell["last"] is None else max(cell["last"], r["d"])

    account_rows = []
    for acct in accounts:
        fig = figures["per_account"].get(acct.name, {"total": 0, "exclusive": 0, "shared": 0})
        days = sorted({r["d"] for r in prompt_buckets if r["a"] == acct.name})
        account_rows.append({
            "name": acct.name,
            "path": str(acct.path).replace(str(store_label), "$STORE"),
            "is_canonical": acct.is_canonical,
            "prompts": {k: fig[k] for k in ("total", "exclusive", "shared")},
            "prompt_chars": char_by_account.get(acct.name, {}),
            "pasted_count": pasted_by_account.get(acct.name, 0),
            "first_seen": days[0] if days else None,
            "last_seen": days[-1] if days else None,
        })

    run["duration_seconds"] = round(time.time() - started, 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": timezone_name,
        "window": {"since": since, "until": until},
        "run": run,
        "accounts": account_rows,
        "totals": {"union": figures["union"], "sum_of_totals": figures["sum_of_totals"]},
        "prompt_buckets": prompt_buckets,
        "token_buckets": token_buckets,
        "sessions": sessions,
        "session_histogram": {k: dict(v) for k, v in histogram.items()},
        "projects": [
            {"a": a, "path": p, "n": c["n"], "first_seen": c["first"], "last_seen": c["last"]}
            for (a, p), c in sorted(project_rows.items(), key=lambda kv: (kv[0][0], -kv[1]["n"]))
        ],
        "tools": _sorted_counts({k: v["tools"] for k, v in merged_by_account.items()}),
        "slash": _sorted_counts(_slash_counts(per_account_entries)),
        "branches": _sorted_counts({k: v["branches"] for k, v in merged_by_account.items()}),
        "versions": _sorted_counts({k: v["versions"] for k, v in merged_by_account.items()}),
    }


def _slash_counts(per_account_entries):
    out = {}
    for name, entries in per_account_entries.items():
        inner = {}
        for e in entries:
            if e["slash"]:
                inner[e["slash"]] = inner.get(e["slash"], 0) + 1
        out[name] = inner
    return out


def _in_window(ts_ms, since, until):
    day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return _in_day_window(day, since, until)


def _in_day_window(day, since, until):
    if since and day < since:
        return False
    if until and day > until:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_aggregate -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/aggregate.py scripts/test_aggregate.py
git commit -m "feat(usage-stats): schema v1 aggregation with losslessness guarantees"
```

---

### Task 6: CLI and renderer

**Files:**
- Create: `scripts/render.py`, `scripts/collect_stats.py`
- Test: `scripts/test_render.py`, `scripts/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `render.render(template_text: str, stats: dict) -> str` — replaces the placeholder with an inline JSON script block.
  - `render.PLACEHOLDER = "<!--STATS_JSON-->"`.
  - `collect_stats.main(argv=None) -> int` — CLI entry; `0` on success.

- [ ] **Step 1: Write the failing tests**

```python
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
```

```python
# scripts/test_cli.py
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("collect_stats.py")
TS = 1786507331000


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        acct = self.store / "solo"
        (acct / "projects").mkdir(parents=True)
        (acct / "history.jsonl").write_text(json.dumps({
            "display": "/code-review go", "timestamp": TS,
            "project": "~/repos/x", "sessionId": "s1", "pastedContents": {}}) + "\n")
        (self.tmp / "canonical").mkdir()
        self.out = self.tmp / "out"

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(self.store),
             "--canonical", str(self.tmp / "canonical"), "--out", str(self.out), *extra],
            capture_output=True, text=True)

    def test_writes_both_artifacts_and_exits_zero(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertEqual(stats["schema_version"], 1)
        self.assertEqual(stats["totals"]["sum_of_totals"], 1)
        html = (self.out / "dashboard.html").read_text()
        self.assertIn('id="stats-data"', html)

    def test_json_only_skips_html(self):
        r = self.run_cli("--json-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.out / "stats.json").exists())
        self.assertFalse((self.out / "dashboard.html").exists())

    def test_second_run_uses_cache(self):
        self.run_cli()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_cache_flag_writes_no_cache_file(self):
        self.run_cli("--no-cache")
        self.assertFalse((self.out / "cache.json").exists())

    def test_accounts_filter(self):
        r = self.run_cli("--accounts", "nosuchaccount")
        self.assertEqual(r.returncode, 0, r.stderr)
        stats = json.loads((self.out / "stats.json").read_text())
        self.assertEqual(stats["accounts"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts && python3 -m unittest test_render test_cli -v`
Expected: FAIL — `No module named 'render'`, and the CLI subprocess failing.

- [ ] **Step 3: Write the renderer**

```python
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
```

- [ ] **Step 4: Write the CLI**

```python
#!/usr/bin/env python3
"""claude-acs stats -- cross-account Claude Code usage statistics.

Emits stats.json plus a standalone dashboard.html. Standard library only.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aggregate          # noqa: E402
import cache as cache_mod # noqa: E402
import discovery          # noqa: E402
import render as render_mod  # noqa: E402
import transcripts        # noqa: E402

DEFAULT_OUT = Path.home() / ".cache" / "claude-acs-stats"
TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"


def _local_timezone_name():
    """IANA zone name, best effort. Never fatal."""
    try:
        from zoneinfo import ZoneInfo  # noqa: F401
        link = Path("/etc/localtime")
        if link.is_symlink():
            target = os.readlink(link)
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return os.environ.get("TZ") or None


def parse_args(argv):
    p = argparse.ArgumentParser(prog="claude-acs stats", description=__doc__)
    p.add_argument("--store", default=str(Path.home() / ".claude-accounts"),
                   help="account store dir (default: ~/.claude-accounts)")
    p.add_argument("--canonical", default=str(Path.home() / ".claude"),
                   help="canonical config dir (default: ~/.claude)")
    p.add_argument("--accounts", help="comma-separated account names to include")
    p.add_argument("--since", help="earliest UTC date to include, YYYY-MM-DD")
    p.add_argument("--until", help="latest UTC date to include, YYYY-MM-DD")
    p.add_argument("--out", default=str(DEFAULT_OUT), help=f"output dir (default: {DEFAULT_OUT})")
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the parse cache")
  # NOTE: Cache.save() writes only keys touched this run, so a filtered run
  # (--accounts / --since) drops cache entries for everything it skipped, and
  # the next full run re-parses them. Document this in the README.
    p.add_argument("--json-only", action="store_true", help="skip dashboard.html")
    p.add_argument("--open", action="store_true", help="open the dashboard when done")
    p.add_argument("--quiet", action="store_true", help="suppress progress output")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    accounts = discovery.discover_accounts(Path(args.store).expanduser(),
                                           Path(args.canonical).expanduser())
    if args.accounts:
        wanted = {n.strip() for n in args.accounts.split(",") if n.strip()}
        accounts = [a for a in accounts if a.name in wanted]

    parse_cache = None
    if not args.no_cache:
        parse_cache = cache_mod.Cache.load(out_dir / "cache.json", transcripts.ROLLUP_SCHEMA)

    def progress(n):
        if not args.quiet and n % 250 == 0:
            print(f"  ... {n} transcripts scanned", file=sys.stderr)

    stats = aggregate.build_stats(
        accounts, store_label=args.store, since=args.since, until=args.until,
        cache=parse_cache, timezone_name=_local_timezone_name(), on_progress=progress)

    if parse_cache:
        parse_cache.save()

    json_path = out_dir / "stats.json"
    import json as _json
    json_path.write_text(_json.dumps(stats, indent=1))

    html_path = out_dir / "dashboard.html"
    if not args.json_only:
        if not TEMPLATE.is_file():
            print(f"error: template not found at {TEMPLATE}", file=sys.stderr)
            return 1
        html_path.write_text(render_mod.render(TEMPLATE.read_text(), stats))

    if not args.quiet:
        run = stats["run"]
        print(f"stats.json  {json_path}  ({json_path.stat().st_size // 1024} KB)")
        if not args.json_only:
            print(f"dashboard   {html_path}")
        print(f"accounts {len(stats['accounts'])}  union {stats['totals']['union']}  "
              f"sum {stats['totals']['sum_of_totals']}")
        print(f"files {run['files_scanned']} scanned, {run['files_parsed']} parsed, "
              f"{run['cache_hits']} cached, {run['malformed_lines']} malformed lines, "
              f"{run['duration_seconds']}s")

    if args.open and not args.json_only:
        import subprocess
        subprocess.run(["open", str(html_path)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Create a placeholder template so the CLI tests can run**

The real dashboard lands in Task 7. For now, the minimum that satisfies the renderer:

```bash
mkdir -p ../dashboard && cat > ../dashboard/template.html <<'HTML'
<title>Claude Code Usage</title>
<body>
<h1>Claude Code Usage</h1>
<!--STATS_JSON-->
</body>
HTML
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scripts && python3 -m unittest test_render test_cli -v`
Expected: 8 tests PASS

- [ ] **Step 7: Run the full suite, then the real thing**

Run: `cd scripts && python3 -m unittest discover -s . -v`
Expected: all tests from Tasks 1–6 PASS.

Then the real machine. Measured cold-parse throughput is 147 MB/s, so the full
1.99 GB pass takes **~14s**, not minutes:
```bash
cd scripts && time python3 collect_stats.py
```
Expected: `union 8232`, ~5726 files scanned, a `stats.json` written. Then run it again and confirm the second run reports nearly all `cached` and completes in seconds.

- [ ] **Step 8: Commit**

```bash
git add scripts/render.py scripts/collect_stats.py scripts/test_render.py scripts/test_cli.py ../dashboard/template.html
git commit -m "feat(usage-stats): CLI entry point and JSON-into-HTML renderer"
```

---

### Task 7: Dashboard

**Files:**
- Modify: `dashboard/template.html` (replace the Task 6 placeholder wholesale)
- Test: `scripts/test_dashboard.py`

**Interfaces:**
- Consumes: `render.PLACEHOLDER`; the schema from Task 5.
- Produces: no Python interface. The page reads `#stats-data`, and must refuse to render when `schema_version !== 1`.

Panels required by the spec: summary cards per account · calendar activity heatmap · prompts/day with 7-day rolling mean · hour-of-day × weekday matrix · stacked token area over time · model mix over time · top tools · top projects · prompts-per-session distribution · slash-command leaderboard · freshness footer. Plus an account filter and the `raw / deduped` toggle.

- [ ] **Step 1: Write the failing test**

The dashboard is HTML+JS, so the test asserts structural contracts rather than pixels — cheap, and it catches the failures that matter (missing placeholder, no version guard, a CDN sneaking in, a `duration` computation).

```python
# scripts/test_dashboard.py
import re, unittest
from pathlib import Path
import render

TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text()

    def test_has_injection_placeholder(self):
        self.assertIn(render.PLACEHOLDER, self.text)

    def test_no_external_requests(self):
        """No CDN, no remote anything -- the page must work offline forever."""
        for pattern in (r"https?://(?!www\.w3\.org)", r"<script[^>]+src=", r"<link[^>]+href=[\"']http"):
            self.assertIsNone(re.search(pattern, self.text), f"external ref matched {pattern}")

    def test_guards_schema_version(self):
        self.assertIn("schema_version", self.text)
        self.assertRegex(self.text, r"schema_version\s*!==?\s*1")

    def test_never_computes_session_duration(self):
        """last_ts - first_ts would present resume artefacts as work sessions."""
        self.assertNotRegex(self.text, r"last_ts\s*-\s*|duration")

    def test_declares_required_panels(self):
        for panel in ["calendar", "prompts-per-day", "hour-weekday", "tokens",
                      "models", "tools", "projects", "sessions", "slash", "footer"]:
            self.assertIn(f'data-panel="{panel}"', self.text, panel)

    def test_has_account_filter_and_dedup_toggle(self):
        self.assertIn('id="account-filter"', self.text)
        self.assertIn('id="dedup-toggle"', self.text)

    def test_renders_with_real_stats_without_placeholder_left(self):
        out = render.render(self.text, {"schema_version": 1, "accounts": [],
                                        "prompt_buckets": [], "token_buckets": [],
                                        "sessions": [], "projects": [], "tools": [],
                                        "slash": [], "branches": [], "versions": [],
                                        "session_histogram": {},
                                        "totals": {"union": 0, "sum_of_totals": 0},
                                        "run": {}, "window": {}, "timezone": "UTC",
                                        "generated_at": "2026-08-22T00:00:00Z"})
        self.assertNotIn(render.PLACEHOLDER, out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && python3 -m unittest test_dashboard -v`
Expected: FAIL — the placeholder template from Task 6 has no panels, no version guard, no filter.

- [ ] **Step 3: Build the dashboard**

Write `dashboard/template.html` as a single self-contained page. Requirements, all load-bearing:

1. **Read the data** from `<script id="stats-data">` via `JSON.parse`. If `schema_version !== 1`, replace the body with a plain message naming the version found and the version expected. Draw nothing.
2. **Theme tokens** on `:root`, with a `prefers-color-scheme: dark` block. Give `body` an explicit background.
3. **Account filter** (`id="account-filter"`) — checkboxes, one per account plus "all". **Dedup toggle** (`id="dedup-toggle"`) — switches every count between the `n` (raw) and `x` (deduped) fields of `prompt_buckets`. Both re-render all panels on change.
4. **Every panel** gets `data-panel="<name>"` using exactly the names asserted in the test.
5. **Convert UTC to local for display** using the `timezone` field: `new Date(Date.UTC(...))` then `toLocaleString(undefined, {timeZone: stats.timezone})`. State on the page that stored data is UTC.
6. **Token panel needs a log scale or a second axis.** `cache_read` runs ~5 orders of magnitude above `input_tokens`; on a shared linear axis every other series flattens to nothing. Label the scale explicitly.
7. **Inline SVG only.** Small helpers — `svgEl(tag, attrs)`, `scaleLinear(domain, range)`, `path(points)`. No library.
8. **Wide panels scroll inside `overflow-x: auto`.** The page body must never scroll sideways.
9. **Never compute a session duration.** Show `first_ts`, `last_ts`, and the `resumed` flag as data. No arithmetic between them.
10. **Freshness footer** — `generated_at`, `files_scanned`, `files_parsed`, `cache_hits`, `malformed_lines`, `duration_seconds`. If `malformed_lines > 0`, style it as a warning: silent parse failures must not read as low activity.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && python3 -m unittest test_dashboard -v`
Expected: 7 tests PASS

- [ ] **Step 5: Render and eyeball it against real data**

```bash
cd scripts && python3 collect_stats.py && open ~/.cache/claude-acs-stats/dashboard.html
```

Check by eye: the account filter toggles panels; the dedup toggle changes `personal2` from 2331 to 1; the calendar shows the March and June–August density; the hour matrix shows both the 09–16 work block and the 00–04 night block; the token panel does not render as one flat line.

- [ ] **Step 6: Commit**

```bash
git add ../dashboard/template.html scripts/test_dashboard.py
git commit -m "feat(usage-stats): standalone dashboard with account filter and dedup toggle"
```

---

### Task 8: `claude-acs stats` wrapper and setup docs

**Files:**
- Modify: `~/claude_code_toggle.sh` (dispatcher `case`, help text, new `_claude_acs_stats` function)
- Create: `README.md`, `INDEX.md`
- Modify: `../INDEX.md` (the `setups/` index)

**Interfaces:**
- Consumes: `scripts/collect_stats.py` from Task 6.
- Produces: `claude-acs stats [args...]` and `claude-acs usage [args...]`.

Note: `~/claude_code_toggle.sh` is **not** tracked in this repo (it lives in `$HOME`, sourced from `~/.zshrc`). Edit it in place; it is not part of the commit.

- [ ] **Step 1: Add the wrapper function**

Insert next to `_claude_acs_apply_profile` in `~/claude_code_toggle.sh`:

```bash
# Cross-account usage statistics. Thin wrapper -- all logic lives in the repo.
# Override the repo location with CLAUDE_ACS_REPO (or the older
# CLAUDE_ACS_PROFILES_REPO, kept working for both setups).
_claude_acs_stats() {
  local repo="${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
  local script="$repo/setups/claude-code-usage-stats/scripts/collect_stats.py"
  if [ ! -f "$script" ]; then
    echo "❌ stats script not found: $script"
    echo "   Set CLAUDE_ACS_REPO to your acs-agentic-setup checkout."
    return 1
  fi
  python3 "$script" --store "$CLAUDE_ACCOUNT_STORE" "$@"
}
```

- [ ] **Step 2: Wire it into the dispatcher**

In the `claude-acs()` `case`, add before the `help` arm:

```bash
    stats|usage)
      _claude_acs_stats "$@"
      ;;
```

- [ ] **Step 3: Add the help line**

In `_claude_acs_help`, after the `apply-profile` row (keep the box-drawing alignment intact):

```
│    stats, usage             Cross-account usage stats + dashboard    │
```

- [ ] **Step 4: Verify the wrapper end to end**

```bash
source ~/claude_code_toggle.sh
claude-acs help | grep -i stats
claude-acs stats --json-only --out /tmp/acs-stats-check
python3 -c "import json;d=json.load(open('/tmp/acs-stats-check/stats.json'));print(d['schema_version'], d['totals'])"
CLAUDE_ACS_REPO=/nonexistent claude-acs stats; echo "exit=$? (expect 1 and a clear hint)"
rm -rf /tmp/acs-stats-check
```
Expected: help shows the row; the run writes valid JSON; the bad-repo case exits 1 with the hint.

- [ ] **Step 5: Write `README.md`**

Match the sibling setup's voice. Cover: what it does; `claude-acs stats` usage with every flag; where output lands and that it is deliberately outside the repo; the three-figure dedup model (total/exclusive/shared) and why both views exist; that all stored times are UTC with an IANA zone recorded; that no per-prompt data is ever written; how to run the tests; and the first-run-is-slow/second-run-is-fast cache behaviour.

- [ ] **Step 6: Write `INDEX.md`**

Mirror `setups/claude-code-account-profiles/INDEX.md`: one-paragraph summary, then a table of `README.md` / `design.md` / `implementation-plan.md` / `scripts/` / `dashboard/`. Add a row for this setup to `setups/INDEX.md`.

- [ ] **Step 7: Full suite green, then commit**

```bash
cd scripts && python3 -m unittest discover -s . -v
cd .. && git add README.md INDEX.md ../INDEX.md
git commit -m "docs(usage-stats): setup README, index, and claude-acs stats wrapper notes"
```

---

## Self-Review

Checked against `design.md` after writing:

**Spec coverage** — every section maps to a task: accounts-in-scope → T1; Layer A, dedup three figures, frozen-prefix invariant, `--since` fast path → T2; Layer B, `iterations[]` and `message.id` traps → T3; incremental cache → T4; schema, privacy (no per-prompt row), session rows without duration, losslessness → T5; output artifacts, CLI flags, repo override → T6; all 11 panels, cache-read scale trap, malformed-line surfacing → T7; wrapper, error handling, docs → T8.

**Deliberate deferrals, both stated in-task rather than hidden:**
- The frozen-prefix invariant (`shared` must never increase between runs) is asserted in T2's *test* fixtures but not enforced across runs at runtime — that needs a stored prior figure, which the cache could carry in a later pass. Flagged, not silently dropped.
- `--since` currently filters after parsing rather than skipping the dedup set math entirely. Correct results, not yet the fast path the spec anticipates. T2 keeps the door open.

**Placeholder scan** — no TBDs; every code step carries runnable code; T7's dashboard is prose-specified because it is HTML+JS, but its ten requirements are individually testable and the test file asserts the structural ones.

**Type consistency** — `Account` fields, `read_history`/`read_history_rows` split, `dedup_figures` returning `exclusive_keys` for T2→T5 handoff, `ROLLUP_SCHEMA` shared T3→T4→T6, and `PLACEHOLDER` shared T6→T7 all line up across tasks.
