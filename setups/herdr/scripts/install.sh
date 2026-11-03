#!/bin/bash
# Install this setup's herdr config onto this machine: copy repo -> live.
#
# Usage: scripts/install.sh [account ...]    (default account: harrison)
#
# - Copies config.toml and the acs.attention plugin into ~/.config/herdr,
#   backing up any differing file as <file>.bak-<timestamp> first.
# - Installs herdr-cmux-notify.sh into each account's hooks/ and wires it to
#   the Notification and Stop events in that account's settings.json (idempotent).
# - Appends the herdr/cmux block to ~/.zshrc once (marker line guards re-runs).
# - Links the plugin and reloads config in every running herdr session.
# Copies, not symlinks: the repo lives in git worktrees that come and go.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$HERE/config"
TS="$(date +%Y%m%d-%H%M%S)"
ACCOUNTS=("${@:-harrison}")

install_file() {  # src dst [mode]
  local src="$1" dst="$2" mode="${3:-644}"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then cp -p "$dst" "$dst.bak-$TS"; echo "  backup: $dst.bak-$TS"; fi
  cp "$src" "$dst"; chmod "$mode" "$dst"; echo "  installed: $dst"
}

command -v herdr >/dev/null || { echo "herdr not installed: brew install herdr" >&2; exit 1; }

echo "herdr config"
install_file "$CFG/herdr/config.toml" "$HOME/.config/herdr/config.toml"
for f in herdr-plugin.toml attention.py; do
  install_file "$CFG/herdr/plugins/attention/$f" "$HOME/.config/herdr/plugins/attention/$f"
done
herdr plugin link "$HOME/.config/herdr/plugins/attention" >/dev/null && echo "  linked: acs.attention"

for acct in "${ACCOUNTS[@]}"; do
  base="$HOME/.claude-accounts/$acct"
  [ -d "$base" ] || { echo "skip account $acct: $base missing" >&2; continue; }
  echo "claude account: $acct"
  install_file "$CFG/claude-hooks/herdr-cmux-notify.sh" "$base/hooks/herdr-cmux-notify.sh" 755
  python3 -I - "$base/settings.json" "$base/hooks/herdr-cmux-notify.sh" "$TS" <<'PY'
import json, shutil, sys
path, hook, ts = sys.argv[1:4]
cmd = f"bash '{hook}'"
with open(path) as fh:
    settings = json.load(fh)
hooks = settings.setdefault("hooks", {})
changed = False
for event in ("Notification", "Stop"):
    groups = hooks.setdefault(event, [])
    if not any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
        changed = True
if changed:
    shutil.copy2(path, f"{path}.bak-{ts}")
    with open(path, "w") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
print(f"  settings.json: {'hook added' if changed else 'already wired'}")
PY
done

echo "zsh"
if grep -q '^# herdr inside cmux:' "$HOME/.zshrc" 2>/dev/null; then
  echo "  ~/.zshrc: block already present"
else
  { echo; cat "$CFG/zsh/herdr-cmux.zsh"; } >> "$HOME/.zshrc"; echo "  ~/.zshrc: block appended"
fi

echo "reload running sessions"
herdr session list 2>/dev/null | awk 'NR>1 && $2=="running" {print $1}' | while read -r s; do
  herdr --session "$s" server reload-config >/dev/null && echo "  reloaded: $s"
done
