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
#   --home-macs M1,M2       home gateway MACs for `config` (created, or appended if `config` has no MACs yet;
#                           left alone if it already lists some). Warns when no MACs are known at all.
#   (NETMON_DIR set at install time is also written into the agent's environment.)
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
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$BINARY" in
  /*) ;;
  *) die "--binary must be an absolute path" ;;
esac
# The path is substituted into XML by sed: refuse anything that could break
# either (sed: | & backslash; XML: < > " ').
for v in "$BINARY" "${NETMON_DIR:-}"; do
  case "$v" in
    *'|'*|*'&'*|*'\'*|*'<'*|*'>'*|*'"'*|*"'"*)
      die "path may not contain | & \\ < > \" or ': $v" ;;
  esac
done

# NETMON_DIR set at install time must reach the agent too, or the agent and
# the CLI would use different data directories.
ENV_XML=
if [ -n "${NETMON_DIR:-}" ]; then
  ENV_XML="<key>EnvironmentVariables</key><dict><key>NETMON_DIR</key><string>$NETMON_DIR</string></dict>"
fi

render() { # $1 = output file
  sed -e "s|@BINARY@|$BINARY|g" -e "s|@ENVIRONMENT@|$ENV_XML|" "$TEMPLATE" > "$1"
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
MAC_RE='([0-9A-Fa-f]{1,2}:){5}[0-9A-Fa-f]{1,2}'
config_has_macs() { # any non-comment line of config that holds a MAC
  [ -f "$DATA_DIR/config" ] && sed 's/#.*//' "$DATA_DIR/config" | grep -Eq "$MAC_RE"
}
if [ -n "$macs" ]; then
  if [ ! -e "$DATA_DIR/config" ]; then
    echo "+ write $DATA_DIR/config (home gateway MACs, mode 0600)"
    if [ "$dry" -eq 0 ]; then
      ( umask 077
        { echo '# netmon config: gateway MACs that mean "home" (one per line)'; echo "$macs"; } > "$DATA_DIR/config" )
    fi
  elif config_has_macs; then
    echo "note: $DATA_DIR/config already lists home MACs; the given ones were NOT added" >&2
  else
    # `netmon sample|report|query` seeds a comments-only config: append to it.
    echo "warning: $DATA_DIR/config exists without MACs; appending the given home MACs" >&2
    echo "+ append home gateway MACs to $DATA_DIR/config"
    if [ "$dry" -eq 0 ]; then
      ( umask 077; echo "$macs" >> "$DATA_DIR/config" )
    fi
  fi
elif ! config_has_macs; then
  echo "warning: no home gateway MACs given (--home-macs / --seed-from) and none in $DATA_DIR/config:" >&2
  echo "         'home' will never be detected and all traffic is tagged 'elsewhere'." >&2
fi

run mkdir -p "$(dirname "$AGENT")"
echo "+ render $TEMPLATE -> $AGENT (via temp file, plutil -lint, then move)"
if [ "$dry" -eq 0 ]; then
  # Render beside the target so the final mv is an atomic same-volume rename,
  # and lint BEFORE touching the installed agent.
  TMP_AGENT=$(mktemp "$(dirname "$AGENT")/.$LABEL.XXXXXX")
  trap 'rm -f "$TMP_AGENT"' EXIT
  render "$TMP_AGENT"
  plutil -lint "$TMP_AGENT" >/dev/null || die "rendered plist failed plutil -lint; installed agent left untouched"
  mv -f "$TMP_AGENT" "$AGENT"
  trap - EXIT
fi
run launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
run launchctl bootstrap "$DOMAIN" "$AGENT"
echo "installed. check: launchctl print $DOMAIN/$LABEL ; rows appear after ~2 minutes:"
echo "  \"$BINARY\" query --from \$(date +%F) --to \$(date +%F) --group-by day --format csv"
