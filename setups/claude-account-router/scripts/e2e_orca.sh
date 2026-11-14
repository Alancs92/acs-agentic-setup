#!/bin/bash
# End-to-end check that Orca's Claude launches go through claude-router.
# Re-run after an Orca update: it proves Orca still hands `claude` to the first one on PATH.
#
# Usage: scripts/e2e_orca.sh [--keep]     (--keep leaves the sandbox folder for inspection)
#
# Closed: a headless `orca serve` on 127.0.0.1 (no pairing) with its own data dir, fake
# `harrison`/`personal` accounts and a fake `claude` that only prints which account it got.
# Orca starts terminal shells through /usr/bin/login, which resets HOME to the real one, so
# the sandbox reaches those shells through ZDOTDIR (kept by `login -p`) and a toggle copy
# whose account store is inside the sandbox. Your real ~/.zshrc, accounts and claude are not
# used. Every sandbox process is killed on exit.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ORCA="${ORCA_CLI:-/Applications/Orca.app/Contents/Resources/bin/orca}"
PORT="${ORCA_E2E_PORT:-6799}"
[ -x "$ORCA" ] || { echo "skip: Orca not installed ($ORCA)"; exit 0; }

X="$(mktemp -d "${TMPDIR:-/tmp}/claude-router-orca-e2e.XXXXXX")"
X="$(cd "$X" && pwd -P)"
H="$X/home"
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

cleanup() {
  local p
  for p in $(pgrep -f "$X" 2>/dev/null); do kill "$p" 2>/dev/null || true; done
  sleep 1
  for p in $(pgrep -f "$X" 2>/dev/null); do kill -9 "$p" 2>/dev/null || true; done
  if [ "$KEEP" = 1 ]; then echo "sandbox kept: $X"; else rm -rf "$X"; fi
}
trap cleanup EXIT

FAILS=0
check() {  # name expected actual
  if [ "$2" = "$3" ]; then echo "  ok   $1: $3"; else echo "  FAIL $1: expected $2, got ${3:-nothing}"; FAILS=$((FAILS + 1)); fi
}

echo "sandbox: $X"
mkdir -p "$H/.claude-accounts" "$X/realbin" "$X/zdot"
for a in harrison personal; do mkdir -p "$H/.claude-accounts/$a"; echo '{}' > "$H/.claude-accounts/$a/settings.json"; done
echo harrison > "$H/.claude-accounts/.default"
sed "s#^export CLAUDE_ACCOUNT_STORE=\"\$HOME/.claude-accounts\"#export CLAUDE_ACCOUNT_STORE=\"$H/.claude-accounts\"#" \
  "$REPO/setups/claude-code-account-profiles/shell/claude_code_toggle.sh" > "$X/toggle.sh"
grep -q "^export CLAUDE_ACCOUNT_STORE=\"$H/" "$X/toggle.sh" || { echo "could not point the toggle at the sandbox store"; exit 1; }

cat > "$X/realbin/claude" <<'EOF'
#!/bin/sh
echo "FAKECLAUDE account=$(basename "${CLAUDE_CONFIG_DIR:-none}") telemetry_off=${DISABLE_TELEMETRY:-0}"
case " $* " in *" --version "*) echo "0.0.0 (Claude Code)"; exit 0;; esac
sleep 120
EOF
chmod +x "$X/realbin/claude"

mkdir -p "$X/router"
cp -R "$HERE/bin" "$HERE/lib" "$X/router/"
mkdir -p "$H/.config/claude-router"
# Routes by the Orca project's display name (set below), not the repo: the side repo's
# remote is not mentioned anywhere, so a pass proves the project lookup inside Orca.
printf 'personal project=Side?Hustle app=orca\n' > "$H/.config/claude-router/rules"
{
  echo "source $X/toggle.sh"
  echo "path=($X/realbin \$path)"
  echo "export CLAUDE_ROUTER_TOGGLE=$X/toggle.sh CLAUDE_ROUTER_RULES=$H/.config/claude-router/rules CLAUDE_ROUTER_LOG=$X/decisions.log CLAUDE_ROUTER_REAL_CLAUDE=$X/realbin/claude"
  sed "s#\$HOME/.local/share/claude-router/bin#$X/router/bin#g" "$HERE/config/zsh/claude-router.zsh"
} > "$X/zdot/.zshrc"

for r in side work; do
  mkdir -p "$H/repos/$r"
  git -C "$H/repos/$r" init -q -b main
  git -C "$H/repos/$r" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
done
git -C "$H/repos/side" remote add origin git@github.com:Alancs92/side.git
git -C "$H/repos/work" remote add origin git@github.com:annaliseai/work.git

clean() {
  env -i HOME="$H" ZDOTDIR="$X/zdot" USER="$USER" LOGNAME="$USER" SHELL=/bin/zsh TMPDIR="${TMPDIR:-/tmp}" \
    LANG=en_US.UTF-8 PATH=/usr/bin:/bin:/usr/sbin:/sbin ORCA_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 "$@"
}
handle() { python3 -I -c 'import sys,json; d=json.load(sys.stdin)["result"]; o=d.get("outcome") or {}; print((d.get("terminal") or o).get("handle",""))'; }
screen() { clean "$ORCA" terminal read --terminal "$1" --cursor 0 --limit 60 --json \
  | python3 -I -c 'import sys,json; [print(l) for l in json.load(sys.stdin)["result"]["terminal"]["tail"] if "FAKECLAUDE" in l]' | head -1; }
acct_of() { sed -n 's/.*account=\([^ ]*\).*/\1/p'; }

echo "orca serve (127.0.0.1:$PORT, no pairing)"
(cd "$H" && clean "$ORCA" serve --bind 127.0.0.1 --port "$PORT" --no-pairing --json > "$X/serve.log" 2>&1 &)
for _ in $(seq 1 60); do grep -q orca_server_ready "$X/serve.log" 2>/dev/null && break; sleep 1; done
grep -q orca_server_ready "$X/serve.log" || { echo "orca serve did not start:"; tail -5 "$X/serve.log"; exit 1; }
for r in side work; do clean "$ORCA" repo add --path "$H/repos/$r" --json >/dev/null; done
SETUP="$(clean "$ORCA" project setups --json | python3 -I -c 'import sys,json; print(next(s["id"] for s in json.load(sys.stdin)["result"]["setups"] if s["path"].endswith("/repos/side")))')"
clean "$ORCA" project setup-update --setup "$SETUP" --display-name "Side Hustle" --json >/dev/null
check "project renamed" "Side Hustle" "$(clean "$ORCA" project list --json | python3 -I -c 'import sys,json; print(" ".join(p["displayName"] for p in json.load(sys.stdin)["result"]["projects"] if "Side" in p["displayName"]))')"

echo "1. claude typed in an Orca terminal (what a restored pane runs)"
for r in side work; do
  clean "$ORCA" terminal create --worktree "path:$H/repos/$r" --command "claude --resume e2e" --json | handle > "$X/h-$r"
done
echo "2. orca agent launch --surface terminal"
clean "$ORCA" agent launch --agent claude --worktree "path:$H/repos/side" --surface terminal --json | handle > "$X/h-launch"
sleep 8
check "side terminal" personal "$(screen "$(cat "$X/h-side")" | acct_of)"
check "work terminal" harrison "$(screen "$(cat "$X/h-work")" | acct_of)"
check "agent launch"  personal "$(screen "$(cat "$X/h-launch")" | acct_of)"
check "side telemetry opt-out" 1 "$(screen "$(cat "$X/h-side")" | sed -n 's/.*telemetry_off=\([^ ]*\).*/\1/p')"

echo "3. orca agent launch --surface chat (Orca spawns claude itself, no shell)"
: > "$X/decisions.log.before"; cp "$X/decisions.log" "$X/decisions.log.before" 2>/dev/null || true
clean "$ORCA" agent launch --agent claude --worktree "path:$H/repos/side" --surface chat --json >/dev/null 2>&1 || true
sleep 6
check "chat launch" personal "$(diff "$X/decisions.log.before" "$X/decisions.log" | sed -n 's/^> [^ ]* \([^ ]*\) .*cwd=[^ ]*repos\/side.*/\1/p' | head -1)"

echo "router decisions:"; sed "s#$X#<sandbox>#g; s#^#  #" "$X/decisions.log"
if [ "$FAILS" -gt 0 ]; then echo "FAILED: $FAILS check(s)"; exit 1; fi
echo "all checks passed"
