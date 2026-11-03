#!/bin/sh
# Claude Notification/Stop hook for panes running inside herdr under cmux.
#
# ~/.zshrc hides cmux's surface IDs inside herdr panes (so cmux's own Claude hooks
# don't hijack session restore) and keeps the hosting cmux tab in HERDR_HOST_CMUX_SURFACE.
# This hook posts the cmux notification itself, against that tab, with herdr context:
#   title    <cmux tab> · herdr-<session> › <space>
#   subtitle the pane's Claude session title
#   body     Claude's message, or "Response ready" on Stop
#
# Usage: herdr-cmux-notify.sh   (reads the hook JSON on stdin)

[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_HOST_CMUX_SURFACE:-}" ] || exit 0
command -v cmux >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

input_file="$(mktemp "${TMPDIR:-/tmp}/herdr-cmux-notify.XXXXXX")" || exit 0
trap 'rm -f "$input_file"' EXIT HUP INT TERM
cat >"$input_file" 2>/dev/null || true

HOOK_INPUT_FILE="$input_file" python3 -I - <<'PY' || true
import json
import os
import subprocess

def run_json(*args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=3).stdout
        return json.loads(out)
    except Exception:
        return {}

def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)

try:
    with open(os.environ["HOOK_INPUT_FILE"], encoding="utf-8") as handle:
        hook = json.load(handle)
except Exception:
    hook = {}

# Subagent events are noise; only the pane's main session notifies.
if hook.get("agent_id"):
    raise SystemExit(0)

event = hook.get("hook_event_name", "")
body = hook.get("message") or ("Response ready" if event == "Stop" else "Claude needs your attention")

surface = os.environ["HERDR_HOST_CMUX_SURFACE"]
session = os.environ.get("HERDR_SESSION", "")
workspace_id = os.environ.get("HERDR_WORKSPACE_ID", "")
pane_id = os.environ.get("HERDR_PANE_ID", "")

# cmux tab title for the hosting surface.
tab_title = ""
for node in walk(run_json("cmux", "tree", "--all", "--id-format", "both", "--json")):
    if node.get("id") == surface and node.get("title"):
        tab_title = node["title"]
        break

# herdr space label.
space = ""
listing = run_json("herdr", "workspace", "list").get("result", {})
for ws in listing.get("workspaces", []):
    if ws.get("workspace_id") == workspace_id:
        space = ws.get("label", "")
        break

# The pane's Claude session title (what Claude puts in the terminal title).
subtitle = ""
agents = run_json("herdr", "agent", "list").get("result", {})
for agent in agents.get("agents", []):
    if agent.get("pane_id") == pane_id:
        subtitle = agent.get("terminal_title_stripped", "")
        break

herdr_part = f"herdr-{session}" if session else "herdr"
if space:
    herdr_part += f" › {space}"
title = f"{tab_title} · {herdr_part}" if tab_title else herdr_part

args = ["cmux", "notify", "--surface", surface, "--title", title, "--body", body]
if subtitle:
    args[4:4] = ["--subtitle", subtitle]
subprocess.run(args, capture_output=True, timeout=5)
PY
exit 0
