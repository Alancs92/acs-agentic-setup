#!/bin/sh
# netmon installer. NOT run automatically: installation is a separate, explicitly
# approved step.
#
#   scripts/install.sh --render OUT.plist [--binary PATH]   render the plist only (touches nothing else)
#   scripts/install.sh --dry-run [options]                  print what an install would do
#   scripts/install.sh [options]                            install + load the launchd agent
#   scripts/install.sh --uninstall                          unload + remove agent and binary (data is kept)
#
# options:
#   --source PATH           built binary to install (default: target/release/netmon next to this script)
#   --home-macs M1,M2       seed `config` with home gateway MACs (only if `config` does not exist yet)
#   --seed-from places.json seed `config` from the "home_gateway_macs" array of claude-ato's places.json
#                           (read once; netmon has no runtime dependency on it)
set -eu

LABEL="dev.netmon.sample"
HERE=$(cd "$(dirname "$0")/.." && pwd)
TEMPLATE="$HERE/launchd/$LABEL.plist.template"
BIN_DIR="${HOME}/.local/bin"
BINARY="$BIN_DIR/netmon"
SOURCE="$HERE/target/release/netmon"
DATA_DIR="${NETMON_DIR:-$HOME/Library/Application Support/netmon}"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"

mode=install
render_out=
dry=0
home_macs=
seed_from=

die() { echo "install.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --render) mode=render; render_out="${2:?--render needs an output path}"; shift 2 ;;
    --binary) BINARY="${2:?--binary needs a path}"; shift 2 ;;
    --source) SOURCE="${2:?--source needs a path}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --uninstall) mode=uninstall; shift ;;
    --home-macs) home_macs="${2:?--home-macs needs a value}"; shift 2 ;;
    --seed-from) seed_from="${2:?--seed-from needs a file}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$BINARY" in
  /*) ;;
  *) die "--binary must be an absolute path" ;;
esac
case "$BINARY" in
  *'|'*|*'&'*|*'\'*) die "binary path may not contain | & or backslash" ;;
esac

render() { # $1 = output file
  sed "s|@BINARY@|$BINARY|g" "$TEMPLATE" > "$1"
}

run() { # print, and execute unless --dry-run
  echo "+ $*"
  if [ "$dry" -eq 0 ]; then "$@"; fi
}

if [ "$mode" = render ]; then
  render "$render_out"
  exit 0
fi

UID_NUM=$(id -u)
DOMAIN="gui/$UID_NUM"

if [ "$mode" = uninstall ]; then
  run launchctl bootout "$DOMAIN/$LABEL" || true
  run rm -f "$AGENT" "$BINARY"
  echo "removed agent and binary; data kept in $DATA_DIR"
  exit 0
fi

# ---- install -------------------------------------------------------------
[ -x "$SOURCE" ] || die "no built binary at $SOURCE (run: cargo build --release)"

macs=
if [ -n "$home_macs" ]; then
  macs=$(echo "$home_macs" | tr ',' '\n')
elif [ -n "$seed_from" ]; then
  [ -r "$seed_from" ] || die "cannot read $seed_from"
  macs=$(awk '/home_gateway_macs/{f=1} f{print} f&&/\]/{exit}' "$seed_from" \
         | grep -Eo '([0-9A-Fa-f]{1,2}:){5}[0-9A-Fa-f]{1,2}' || true)
fi

run mkdir -p "$BIN_DIR"
run install -m 0755 "$SOURCE" "$BINARY"
run mkdir -p "$DATA_DIR"
run chmod 700 "$DATA_DIR"
if [ -n "$macs" ] && [ ! -e "$DATA_DIR/config" ]; then
  echo "+ write $DATA_DIR/config (home gateway MACs, mode 0600)"
  if [ "$dry" -eq 0 ]; then
    ( umask 077
      { echo '# netmon config: gateway MACs that mean "home" (one per line)'; echo "$macs"; } > "$DATA_DIR/config" )
  fi
fi

run mkdir -p "$(dirname "$AGENT")"
echo "+ render $TEMPLATE -> $AGENT"
if [ "$dry" -eq 0 ]; then
  render "$AGENT"
  plutil -lint "$AGENT" >/dev/null
fi
run launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
run launchctl bootstrap "$DOMAIN" "$AGENT"
echo "installed. check: launchctl print $DOMAIN/$LABEL ; rows appear after ~2 minutes:"
echo "  \"$BINARY\" query --from \$(date +%F) --to \$(date +%F) --group-by day --format csv"
