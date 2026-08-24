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
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue

            # ---- Per-LINE accounting -------------------------------------
            # One assistant MESSAGE is written as several JSONL lines, one per
            # content block (thinking / text / tool_use), all sharing
            # message.id. Each line carries a DISTINCT block, so tool_use must
            # be counted per line -- deduping here would lose most tool calls.
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name") or "(unnamed)"
                        out["tools"][name] = out["tools"].get(name, 0) + 1

            # ---- Per-MESSAGE accounting ----------------------------------
            # Everything below counts messages, not lines. Without this gate,
            # `turns` and the branch/version/project counts inflate ~2x (in
            # real data: 688 lines for 341 unique ids), and token totals inflate
            # the same way because every duplicate line repeats the full usage
            # figures verbatim.
            mid = msg.get("id")
            if mid is not None:
                if mid in seen_message_ids:
                    continue
                seen_message_ids.add(mid)

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

            model = msg.get("model")
            usage = msg.get("usage")
            if not isinstance(usage, dict) or not model:
                continue

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
