#!/bin/bash
# Copy the live herdr config back into this repo (live -> repo), for committing
# changes made in place. Shows the resulting diff; commits nothing.
#
# Usage: scripts/capture.sh [account]    (default account: harrison)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$HERE/config"
ACCT="${1:-harrison}"

cp "$HOME/.config/herdr/config.toml" "$CFG/herdr/config.toml"
cp "$HOME/.config/herdr/plugins/attention/herdr-plugin.toml" "$HOME/.config/herdr/plugins/attention/attention.py" \
   "$CFG/herdr/plugins/attention/"
cp "$HOME/.claude-accounts/$ACCT/hooks/herdr-cmux-notify.sh" "$CFG/claude-hooks/"
sed -n '/^# herdr inside cmux:/,/^fi$/p' "$HOME/.zshrc" > "$CFG/zsh/herdr-cmux.zsh"

git -C "$HERE" status --short -- .
