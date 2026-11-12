#!/bin/bash
# Install this setup's herdr config onto this machine: copy repo -> live.
#
# Usage: scripts/install.sh [account ...]
#   With no accounts, installs for every saved account: each directory under
#   $CLAUDE_ACCOUNT_STORE (default ~/.claude-accounts) that has a settings.json.
#   herdr sessions route to accounts by name (see config/zsh/herdr-cmux.zsh), so
#   any account can end up running Claude inside herdr and needs the hook.
#
# - Copies config.toml and the acs.attention plugin into ~/.config/herdr,
#   backing up any differing file as <file>.bak-<timestamp> first.
# - Installs herdr-cmux-notify.sh into each account's hooks/ and wires it to
#   the Notification and Stop events in that account's settings.json (idempotent).
#   An account without a settings.json is skipped with a message.
# - Writes the managed herdr-cmux block into ~/.zshrc: appended the first time,
#   replaced in place on later runs, so edits in the repo reach the live file.
# - Links the plugin and reloads config in every running herdr session.
#   A failed reload is a warning: the files are installed either way.
# Copies, not symlinks: the repo lives in git worktrees that come and go.
#
# Exits non-zero if an account's settings.json or ~/.zshrc could not be updated.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$HERE/config"
TS="$(date +%Y%m%d-%H%M%S)-$$"  # pid suffix: two runs in one second must not share a backup name
STORE="${CLAUDE_ACCOUNT_STORE:-$HOME/.claude-accounts}"
FAILED=0

warn() { echo "  warning: $*" >&2; }

install_file() {  # src dst [mode]
  local src="$1" dst="$2" mode="${3:-644}"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then cp -p "$dst" "$dst.bak-$TS"; echo "  backup: $dst.bak-$TS"; fi
  cp "$src" "$dst"; chmod "$mode" "$dst"; echo "  installed: $dst"
}

# Account names, space-separated. A plain string, not an array: macOS /bin/bash is
# 3.2, where expanding an empty array trips `set -u`.
if [ $# -gt 0 ]; then
  ACCOUNTS="$*"
else
  ACCOUNTS=""
  for dir in "$STORE"/*/; do
    if [ -f "$dir/settings.json" ]; then ACCOUNTS="${ACCOUNTS:+$ACCOUNTS }$(basename "$dir")"; fi
  done
  [ -n "$ACCOUNTS" ] || warn "no saved accounts with a settings.json under $STORE"
fi

command -v herdr >/dev/null || { echo "herdr not installed: brew install herdr" >&2; exit 1; }

echo "herdr config"
install_file "$CFG/herdr/config.toml" "$HOME/.config/herdr/config.toml"
for f in herdr-plugin.toml attention.py; do
  install_file "$CFG/herdr/plugins/attention/$f" "$HOME/.config/herdr/plugins/attention/$f"
done
if herdr plugin link "$HOME/.config/herdr/plugins/attention" >/dev/null 2>&1; then
  echo "  linked: acs.attention"
else
  warn "herdr plugin link failed; run: herdr plugin link $HOME/.config/herdr/plugins/attention"
fi

for acct in $ACCOUNTS; do
  if ! printf '%s' "$acct" | grep -qE '^[A-Za-z0-9._-]+$'; then
    echo "skip account $acct: invalid name" >&2; continue
  fi
  base="$STORE/$acct"
  [ -d "$base" ] || { echo "skip account $acct: $base missing" >&2; continue; }
  [ -f "$base/settings.json" ] || { echo "skip account $acct: no settings.json in $base (start claude once with it first)" >&2; continue; }
  echo "claude account: $acct"
  # herdr's own Claude integration (agent state + session ids, which the marks key on)
  # lives in each account's config dir, so it is per account too.
  # Not `| grep -q`: grep -q exits at the first match, the writer then dies of SIGPIPE,
  # and under pipefail the check fails at random, forcing a needless reinstall.
  if CLAUDE_CONFIG_DIR="$base" herdr integration status 2>/dev/null | grep '^claude: current' >/dev/null; then
    echo "  herdr integration: current"
  elif CLAUDE_CONFIG_DIR="$base" herdr integration install claude >/dev/null 2>&1; then
    echo "  herdr integration: installed"
  else
    warn "herdr integration install failed for $acct; run: CLAUDE_CONFIG_DIR=$base herdr integration install claude"
  fi
  install_file "$CFG/claude-hooks/herdr-cmux-notify.sh" "$base/hooks/herdr-cmux-notify.sh" 755
  if ! python3 -I - "$base/settings.json" "$base/hooks/herdr-cmux-notify.sh" "$TS" <<'PY'
import json, shutil, sys
path, hook, ts = sys.argv[1:4]
cmd = f"bash '{hook}'"
try:
    with open(path, encoding="utf-8") as fh:
        settings = json.load(fh)
except (OSError, ValueError) as exc:
    sys.exit(f"  error: cannot read {path}: {exc}")
hooks = settings.setdefault("hooks", {})
changed = False
for event in ("Notification", "Stop"):
    groups = hooks.setdefault(event, [])
    if not any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
        changed = True
if changed:
    shutil.copy2(path, f"{path}.bak-{ts}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
print(f"  settings.json: {'hook added' if changed else 'already wired'}")
PY
  then
    echo "  error: hook not wired for $acct" >&2; FAILED=1
  fi
done

echo "zsh"
if ! python3 -I - "$HOME/.zshrc" "$CFG/zsh/herdr-cmux.zsh" "$TS" <<'PY'
import os, shutil, sys
path, block_path, ts = sys.argv[1:4]
BEGIN, END = "# >>> herdr-cmux >>>", "# <<< herdr-cmux <<<"
LEGACY = "# herdr inside cmux:"  # pre-marker block, which ended at its first `fi`

with open(block_path, encoding="utf-8") as fh:
    block = fh.read()
if not block.endswith("\n"):
    block += "\n"
try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
except FileNotFoundError:
    text = ""
lines = text.splitlines(keepends=True)

def first(pred, start=0):
    return next((i for i in range(start, len(lines)) if pred(lines[i])), None)

start = first(lambda l: l.startswith(BEGIN))
if start is not None:
    end = first(lambda l: l.rstrip("\n") == END, start)
    if end is None:
        sys.exit(f"  error: {path} has '{BEGIN}' but no '{END}'; fix it by hand, nothing changed")
    how = "block updated"
else:
    start = first(lambda l: l.startswith(LEGACY))
    if start is not None:
        end = first(lambda l: l.rstrip("\n") == "fi", start)
        if end is None:
            sys.exit(f"  error: {path} has an old herdr block with no closing 'fi'; nothing changed")
        how = "old block replaced"
if start is not None:
    new = "".join(lines[:start]) + block + "".join(lines[end + 1:])
else:
    sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
    new, how = text + sep + block, "block appended"

if new == text:
    print("  ~/.zshrc: block up to date")
else:
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{ts}")
    with open(path, "w", encoding="utf-8") as fh:  # in place: keeps a symlinked ~/.zshrc a symlink
        fh.write(new)
    print(f"  ~/.zshrc: {how} (open herdr panes pick it up when their shell restarts)")
PY
then
  FAILED=1
fi

echo "reload running sessions"
if sessions="$(herdr session list 2>/dev/null)"; then
  for s in $(printf '%s\n' "$sessions" | awk 'NR>1 && $2=="running" {print $1}'); do
    if herdr --session "$s" server reload-config >/dev/null 2>&1; then
      echo "  reloaded: $s"
    else
      warn "reload failed for session $s; run: herdr --session $s server reload-config"
    fi
  done
else
  warn "herdr session list failed; reload running sessions by hand"
fi

exit "$FAILED"
