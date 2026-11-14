#!/bin/bash
# Install claude-router onto this machine: copy repo -> live.
#
# Usage: scripts/install.sh
# - Copies bin/ and lib/ to ~/.local/share/claude-router (backing up any differing file
#   as <file>.bak-<timestamp>-<pid>). Copies, not symlinks: the repo lives in worktrees.
# - Creates ~/.config/claude-router/rules from config/rules.example if it does not exist.
#   An existing rules file is never touched.
# - Writes the managed claude-router block into ~/.zshrc: appended the first time, replaced
#   in place later. It must come after `source ~/claude_code_toggle.sh`; appending does that.
# Open shells pick it up when they restart. Undo: delete the block, or CLAUDE_ROUTER_DISABLE=1.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date +%Y%m%d-%H%M%S)-$$"
DEST="$HOME/.local/share/claude-router"
RULES="${XDG_CONFIG_HOME:-$HOME/.config}/claude-router/rules"

install_file() {  # src dst mode
  local src="$1" dst="$2" mode="$3"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then echo "  current: $dst"; return; fi
  if [ -f "$dst" ]; then cp -p "$dst" "$dst.bak-$TS"; echo "  backup: $dst.bak-$TS"; fi
  cp "$src" "$dst"; chmod "$mode" "$dst"; echo "  installed: $dst"
}

echo "claude-router"
install_file "$HERE/lib/claude-router.zsh" "$DEST/lib/claude-router.zsh" 644
install_file "$HERE/bin/claude" "$DEST/bin/claude" 755
install_file "$HERE/bin/claude-route" "$DEST/bin/claude-route" 755
if [ -e "$RULES" ]; then
  echo "  rules: $RULES (kept)"
else
  mkdir -p "$(dirname "$RULES")"; cp "$HERE/config/rules.example" "$RULES"; echo "  rules: $RULES (created, all commented out)"
fi

echo "zsh"
python3 -I - "$HOME/.zshrc" "$HERE/config/zsh/claude-router.zsh" "$TS" <<'PY'
import os, shutil, sys
path, block_path, ts = sys.argv[1:4]
BEGIN, END = "# >>> claude-router >>>", "# <<< claude-router <<<"
TOGGLE = "claude_code_toggle.sh"
block = open(block_path, encoding="utf-8").read()
if not block.endswith("\n"):
    block += "\n"
try:
    text = open(path, encoding="utf-8").read()
except FileNotFoundError:
    text = ""
lines = text.splitlines(keepends=True)
start = next((i for i, l in enumerate(lines) if l.startswith(BEGIN)), None)
if start is not None:
    end = next((i for i in range(start, len(lines)) if lines[i].rstrip("\n") == END), None)
    if end is None:
        sys.exit(f"  error: {path} has '{BEGIN}' but no '{END}'; fix it by hand, nothing changed")
    before = "".join(lines[:start])
    new, how = before + block + "".join(lines[end + 1:]), "block updated"
else:
    before = text
    sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
    new, how = text + sep + block, "block appended"
if TOGGLE not in before:
    print(f"  warning: no 'source ~/{TOGGLE}' above the block; the router's `claude-acs sw` hook stays off", file=sys.stderr)
if new == text:
    print("  ~/.zshrc: block up to date")
else:
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{ts}")
    with open(path, "w", encoding="utf-8") as fh:  # in place: keeps a symlinked ~/.zshrc a symlink
        fh.write(new)
    print(f"  ~/.zshrc: {how} (open shells pick it up when they restart)")
PY
echo "check: zsh -ic 'claude-route which'"
