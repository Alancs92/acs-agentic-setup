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
