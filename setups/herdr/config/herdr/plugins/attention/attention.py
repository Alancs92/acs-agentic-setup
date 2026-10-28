"""Sticky manual marks for herdr agents, plus attention navigation.

herdr's own states (working / blocked / done / idle) are automatic: `done` clears the
moment you view a pane, and herdr has no API to re-arm it. This plugin adds a second,
manual mark per pane (later / review / waiting) as a display-only metadata token that
the sidebar renders via `$mark`. Nothing clears it except you.

Marks are stored by Claude session id when the pane has one (stable across herdr
restarts, when herdr resumes the agent into a possibly different pane), otherwise by
pane id. A [[startup]] hook re-applies them once the agents have reported in.

Usage: attention.py cycle|clear|next|seen-all|restore|restore-loop
"""

import json
import os
import subprocess
import sys
import time

SOURCE = "acs.attention"
TOKEN = "mark"
MARKS = ["later", "review", "waiting"]
LABELS = {"later": "↩️ later", "review": "👀 review", "waiting": "⏳ waiting"}
# Lower rank = more urgent when jumping.
NATIVE_RANK = {"blocked": 0, "done": 1}
MARK_RANK = 2

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.local/state/herdr-attention")
STATE_FILE = os.path.join(STATE_DIR, "marks.json")


def herdr(*args, check=False):
    proc = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=10)
    if check and proc.returncode != 0:
        raise SystemExit(f"herdr {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        return {}


def session_name():
    sock = os.environ.get("HERDR_SOCKET_PATH", "")
    parent = os.path.basename(os.path.dirname(sock))
    if os.path.basename(os.path.dirname(os.path.dirname(sock))) == "sessions":
        return parent
    return "default"


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, STATE_FILE)


def panes():
    return herdr("pane", "list").get("result", {}).get("panes", [])


def mark_key(pane):
    session = (pane.get("agent_session") or {}).get("value")
    return f"session:{session}" if session else f"pane:{pane['pane_id']}"


def target_pane(all_panes):
    """The pane the action was invoked on: explicit env, then plugin context, then focused."""
    wanted = os.environ.get("HERDR_PANE_ID")
    if not wanted:
        try:
            ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
        except json.JSONDecodeError:
            ctx = {}
        pane_ctx = ctx.get("pane") or ctx.get("focused_pane") or {}
        wanted = pane_ctx.get("pane_id") if isinstance(pane_ctx, dict) else None
    for pane in all_panes:
        if wanted and pane["pane_id"] == wanted:
            return pane
    return next((p for p in all_panes if p.get("focused")), None)


def apply(pane_id, mark):
    if mark:
        herdr("pane", "report-metadata", pane_id, "--source", SOURCE, "--token", f"{TOKEN}={LABELS[mark]}", check=True)
    else:
        herdr("pane", "report-metadata", pane_id, "--source", SOURCE, "--clear-token", TOKEN, check=True)


def set_mark(pane, mark):
    state = load_state()
    marks = state.setdefault(session_name(), {})
    key = mark_key(pane)
    # Drop a stale pane-keyed entry once the pane has a session id.
    marks.pop(f"pane:{pane['pane_id']}", None)
    if mark:
        marks[key] = {"mark": mark, "pane_id": pane["pane_id"]}
    else:
        marks.pop(key, None)
    save_state(state)
    apply(pane["pane_id"], mark)


def current_mark(pane):
    marks = load_state().get(session_name(), {})
    entry = marks.get(mark_key(pane)) or marks.get(f"pane:{pane['pane_id']}")
    return entry["mark"] if entry else None


def cmd_cycle():
    pane = target_pane(panes())
    if not pane:
        raise SystemExit("no target pane")
    mark = current_mark(pane)
    order = [None, *MARKS]
    set_mark(pane, order[(order.index(mark) + 1) % len(order)])


def cmd_clear():
    pane = target_pane(panes())
    if pane:
        set_mark(pane, None)


def restore_once():
    """Re-apply stored marks; returns how many are still unmatched."""
    marks = load_state().get(session_name(), {})
    by_key = {mark_key(p): p for p in panes()}
    unmatched = 0
    for key, entry in marks.items():
        pane = by_key.get(key)
        if pane:
            apply(pane["pane_id"], entry["mark"])
        else:
            unmatched += 1
    return unmatched


def cmd_restore():
    # Agents report their session ids a little after the server starts, so retry in
    # the background instead of blocking server startup.
    if restore_once():
        subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), "restore-loop"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def cmd_restore_loop():
    for _ in range(18):
        time.sleep(5)
        if not restore_once():
            return


def cmd_next():
    all_panes = panes()
    marks = load_state().get(session_name(), {})

    def rank(pane):
        native = NATIVE_RANK.get(pane.get("agent_status"))
        if native is not None:
            return native
        if mark_key(pane) in marks or f"pane:{pane['pane_id']}" in marks:
            return MARK_RANK
        return None

    candidates = [p for p in all_panes if rank(p) is not None and not p.get("focused")]
    if not candidates:
        return
    best = min(rank(p) for p in candidates)
    tier = [p for p in candidates if rank(p) == best]
    # Rotate within the tier, starting after the focused pane, so repeated presses walk the list.
    ids = [p["pane_id"] for p in all_panes]
    focused = next((i for i, p in enumerate(all_panes) if p.get("focused")), -1)
    tier.sort(key=lambda p: (ids.index(p["pane_id"]) - focused - 1) % len(ids))
    herdr("agent", "focus", tier[0]["pane_id"], check=True)


def cmd_seen_all():
    all_panes = panes()
    origin = next((p["pane_id"] for p in all_panes if p.get("focused")), None)
    done = [p["pane_id"] for p in all_panes if p.get("agent_status") == "done"]
    for pane_id in done:
        herdr("agent", "focus", pane_id)
    if origin and done:
        herdr("agent", "focus", origin)


COMMANDS = {
    "cycle": cmd_cycle,
    "clear": cmd_clear,
    "next": cmd_next,
    "seen-all": cmd_seen_all,
    "restore": cmd_restore,
    "restore-loop": cmd_restore_loop,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(__doc__)
    COMMANDS[sys.argv[1]]()
