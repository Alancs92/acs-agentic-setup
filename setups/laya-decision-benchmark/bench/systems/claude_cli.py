"""Claude baselines via Claude Code headless: one isolated, tool-less, context-free call per item.

Flags were verified against `claude --help` on 2.1.283: `--tools ""` (no tools), `--setting-sources ""`
(no user/project settings), an empty strict MCP config, and an empty temp cwd (no CLAUDE.md). `--bare` is
deliberately not used: it skips the OAuth login, and these calls should run on the subscription.
"""
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from common import question

SYSTEM = "You are a classifier. Answer only with the requested JSON."
CMD_TEMPLATE = ["claude", "-p", "<PROMPT>", "--model", "<MODEL>", "--output-format", "json",
                "--json-schema", "<SCHEMA>", "--max-turns", "2", "--tools", "", "--setting-sources", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
                "--append-system-prompt", SYSTEM]


def prompt_and_schema(r):
    qk, q = question(r)
    if q["type"] == "noul":
        body = (f"Question (yes/no): {q['instructions']}\n\nText:\n{r['state']['text']}\n\n"
                "Give p_yes, your probability (0 to 1) that the answer is yes.")
        schema = {"type": "object", "properties": {"p_yes": {"type": "number", "minimum": 0, "maximum": 1}},
                  "required": ["p_yes"], "additionalProperties": False}
    else:
        opts = "\n".join(f"- {k}: {v}" for k, v in q["criteria"].items())
        body = (f"Question: {q['instructions']}\n\nOptions:\n{opts}\n\nText:\n{r['state']['text']}\n\n"
                "Pick exactly one option label and give your confidence (0 to 1) that it is correct.")
        schema = {"type": "object",
                  "properties": {"label": {"type": "string", "enum": list(q["criteria"])},
                                 "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
                  "required": ["label", "confidence"], "additionalProperties": False}
    return body, schema


def _parse(env, q):
    out = env.get("structured_output")
    if out is None:
        txt = env.get("result", "")
        s, e = txt.find("{"), txt.rfind("}")
        out = json.loads(txt[s:e + 1]) if s >= 0 else None
    if q["type"] == "noul":
        p = float(out["p_yes"])
        return p >= 0.5, p
    lab, conf = out["label"], float(out["confidence"])
    if lab not in q["criteria"]:
        raise ValueError(lab)
    k = len(q["criteria"])
    p = {l: (conf if l == lab else (1 - conf) / (k - 1)) for l in q["criteria"]}
    return lab, p


def call_one(r, model, cwd):
    qk, q = question(r)
    body, schema = prompt_and_schema(r)
    cmd = [x.replace("<MODEL>", model) if x == "<MODEL>" else x for x in CMD_TEMPLATE]
    cmd[cmd.index("<PROMPT>")] = "".join(c for c in body if c in "\n\t" or ord(c) >= 32)
    cmd[cmd.index("<SCHEMA>")] = json.dumps(schema)
    delay = 20
    for attempt in range(8):
        t = time.perf_counter()
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            continue
        wall = (time.perf_counter() - t) * 1000
        try:
            env = json.loads(p.stdout)
        except json.JSONDecodeError:
            env = {"is_error": True, "result": (p.stdout + p.stderr)[-400:]}
        err = str(env.get("result", "")) + p.stderr
        if env.get("is_error") and any(s in err.lower() for s in ("rate", "limit", "overloaded", "429", "529")):
            time.sleep(delay)
            delay = min(delay * 2, 900)
            continue
        if env.get("is_error") and ("auth" in err.lower() or "login" in err.lower()):
            raise RuntimeError(f"S2 auth failure: {err[:200]}")
        try:
            lab, prob = _parse(env, q)
        except Exception:  # noqa: BLE001 — malformed: retry once, then abstain
            if attempt == 0:
                continue
            lab, prob = None, None
        return {"id": r["id"], "label": lab, "p": prob, "latency_ms": env.get("duration_ms"),
                "wall_ms": wall, "cost_usd": env.get("total_cost_usd"),
                "model_id": ",".join((env.get("modelUsage") or {}).keys()),
                "input_tokens": sum((env.get("usage") or {}).get(k, 0) or 0 for k in
                                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))}
    raise RuntimeError("S2 rate limit persisted")


def run(records, model, done_ids=(), on_row=None, workers=4):
    todo = [r for r in records if r["id"] not in set(done_ids)]
    cwd = tempfile.mkdtemp(prefix="laya-claude-")
    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for row in ex.map(lambda r: call_one(r, model, cwd), todo):
            rows.append(row)
            if on_row:
                on_row(row)
    return rows


def command_line():
    return " ".join(("'" + c + "'") if (c == "" or " " in c or "{" in c) else c for c in CMD_TEMPLATE)
