# scripts/aggregate.py
"""Assemble stats.json. This module owns the schema."""
import time
from collections import defaultdict
from datetime import datetime, timezone

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
                # Copy the list-valued fields too: a shallow dict() would alias
                # them into the source rollup, and a later merge appending to
                # them would corrupt the cached entry we were handed.
                dst = dict(s)
                for f in ("models", "branches"):
                    dst[f] = list(s.get(f) or [])
                out["sessions"][sid] = dst
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
            row = {"a": name, "d": day, "m": model}
            row.update(cell)
            token_buckets.append(row)

    sessions = []
    for name, merged in sorted(merged_by_account.items()):
        for sid, s in sorted(merged["sessions"].items()):
            row = {
                "a": name, "id": sid, "p": s.get("project"),
                "prompts": prompts_per_session[name].get(sid, 0),
                "turns": s.get("turns", 0), "sidechain_turns": s.get("sidechain_turns", 0),
                "first_ts": s["first_ts"], "last_ts": s["last_ts"],
                "resumed": bool(s.get("resumed")),
                "models": sorted(s.get("models") or []),
                "branches": sorted(s.get("branches") or []),
            }
            for f in ("in", "out", "cr", "cw"):
                row[f] = s.get(f, 0)
            sessions.append(row)

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
