# >>> claude-router >>> managed by acs-agentic-setup setups/claude-account-router/scripts/install.sh; edit it there, not here.
# Keep it after `source ~/claude_code_toggle.sh` and the herdr-cmux block (install.sh appends it at the end).
# `claude` on PATH becomes the router, which picks the claude-acs account per launch: claude-route which.
path=("$HOME/.local/share/claude-router/bin" ${path:#$HOME/.local/share/claude-router/bin})
# The account this shell starts on is the default, not a choice, so routing rules may override it...
export CLAUDE_ROUTER_SHELL=1
# ...until you switch by hand: then your choice wins over the rules.
if (( $+functions[claude-acs] )) && (( ! $+functions[_claude_router_acs_orig] )); then
  functions -c claude-acs _claude_router_acs_orig
  claude-acs() {
    _claude_router_acs_orig "$@" || return
    case $1 in switch|sw) unset CLAUDE_ROUTER_SHELL ;; esac
  }
fi
# <<< claude-router <<<
