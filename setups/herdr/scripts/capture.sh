#!/bin/bash
# Copy the live herdr config back into this repo (live -> repo), for committing
# changes made in place. Shows the resulting diff; commits nothing.
#
# Usage: scripts/capture.sh [account]
#   The notify hook is the same file in every account. With no account, it is taken
#   from the first saved account (under $CLAUDE_ACCOUNT_STORE, default
#   ~/.claude-accounts) that has it installed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$HERE/config"
STORE="${CLAUDE_ACCOUNT_STORE:-$HOME/.claude-accounts}"
HOOK=hooks/herdr-cmux-notify.sh

if [ $# -gt 0 ]; then
  src="$STORE/$1/$HOOK"
  [ -f "$src" ] || { echo "no hook at $src" >&2; exit 1; }
else
  src=""
  for dir in "$STORE"/*/; do
    if [ -f "$dir$HOOK" ]; then src="$dir$HOOK"; break; fi
  done
  [ -n "$src" ] || { echo "no account under $STORE has $HOOK installed" >&2; exit 1; }
fi

cp "$HOME/.config/herdr/config.toml" "$CFG/herdr/config.toml"
cp "$HOME/.config/herdr/plugins/attention/herdr-plugin.toml" "$HOME/.config/herdr/plugins/attention/attention.py" \
   "$CFG/herdr/plugins/attention/"
cp "$src" "$CFG/claude-hooks/"
echo "hook captured from: $src"

block="$(sed -n '/^# >>> herdr-cmux >>>/,/^# <<< herdr-cmux <<<$/p' "$HOME/.zshrc")"
if [ -n "$block" ]; then
  printf '%s\n' "$block" > "$CFG/zsh/herdr-cmux.zsh"
else
  echo "$HOME/.zshrc has no managed herdr-cmux block; run scripts/install.sh first" >&2
fi

git -C "$HERE" status --short -- . 2>/dev/null || true
