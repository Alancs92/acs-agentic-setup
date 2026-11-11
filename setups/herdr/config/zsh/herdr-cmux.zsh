# >>> herdr-cmux >>> managed by acs-agentic-setup setups/herdr/scripts/install.sh; edit it there, not here.
# Keep it after `source ~/claude_code_toggle.sh`: the account routing below calls claude-acs.

# 1. herdr inside cmux: hide cmux's surface IDs from herdr panes so cmux's Claude hooks
#    don't record herdr's sessions against the hosting tab (which makes cmux restore
#    `claude --resume` there instead of re-running herdr). Each account's
#    hooks/herdr-cmux-notify.sh posts notifications against HERDR_HOST_CMUX_SURFACE instead.
if [[ -n $HERDR_ENV && -n $CMUX_SURFACE_ID ]]; then
  export HERDR_HOST_CMUX_SURFACE=$CMUX_SURFACE_ID
  unset CMUX_SURFACE_ID CMUX_PANEL_ID CMUX_TAB_ID CMUX_WORKSPACE_ID
fi

# 2. Route a herdr session to a Claude account by name. A session named <account>, or ending
#    in -<account>, switches this shell to that account (longest match wins), so
#    `herdr --session personal` and `herdr --session management-personal` both run Claude
#    as `personal`. Any other session keeps the default account. An account is a directory
#    under $CLAUDE_ACCOUNT_STORE that has a settings.json.
_herdr_session_account() {  # <session> <account-store>
  local session=$1 store=$2 dir name best=
  for dir in "$store"/*(N/); do
    name=${dir:t}
    [[ -f $dir/settings.json ]] || continue
    if [[ $session == $name || $session == *-$name ]] && (( ${#name} > ${#best} )); then
      best=$name
    fi
  done
  print -r -- "$best"
}

if [[ -n $HERDR_ENV && -n $HERDR_SESSION ]] && (( $+functions[claude-acs] )); then
  _herdr_account=$(_herdr_session_account "$HERDR_SESSION" "${CLAUDE_ACCOUNT_STORE:-$HOME/.claude-accounts}")
  if [[ -n $_herdr_account && $_herdr_account != $CLAUDE_ACCOUNT ]]; then
    claude-acs switch "$_herdr_account" >/dev/null
  fi
  unset _herdr_account
fi
# <<< herdr-cmux <<<
