# claude-router: decides which claude-acs account a `claude` launch runs on.
# Sourced (zsh only) by bin/claude and bin/claude-route; defines functions, runs nothing.
#
# Order, first hit wins:
#   1. CLAUDE_ROUTE_ACCOUNT          a one-off or automation override
#   2. an account already chosen     CLAUDE_CONFIG_DIR set by `claude-acs sw`, herdr,
#                                    a script, ... (see _cr_dir_is_auto for what is NOT a choice)
#   3. a repo pin                    git config claude.account (shared by every worktree)
#   4. the rules file                first matching line
#   5. nothing matched               the shell's account stays, or the default if none
#
# Results: CR_ACCOUNT (empty = run as is), CR_ACTION (keep|switch), CR_REASON.

: ${CLAUDE_ACCOUNT_STORE:=$HOME/.claude-accounts}
: ${CLAUDE_ROUTER_RULES:=${XDG_CONFIG_HOME:-$HOME/.config}/claude-router/rules}
: ${CLAUDE_ROUTER_TOGGLE:=$HOME/claude_code_toggle.sh}
: ${CLAUDE_ROUTER_LOG:=${XDG_STATE_HOME:-$HOME/.local/state}/claude-router/decisions.log}
# Config dirs another tool chose for us (Orca's managed accounts). Rules override these.
: ${CLAUDE_ROUTER_MANAGED_DIRS:='*/orca/claude-profiles/*'}

typeset -g CR_ACCOUNT CR_ACTION CR_REASON CR_CWD
typeset -gA CR_FACTS

_cr_valid_name() { [[ $1 == [A-Za-z0-9._-]## ]] }

_cr_default_account() {
  local f=$CLAUDE_ACCOUNT_STORE/.default
  [[ -r $f ]] && print -r -- "${$(<$f)//[[:space:]]/}"
}

_cr_account_exists() { _cr_valid_name "$1" && [[ -d $CLAUDE_ACCOUNT_STORE/$1 ]] }

# Name of the store account a config dir is, or nothing.
_cr_account_of_dir() {
  local d=${1:A} store=${CLAUDE_ACCOUNT_STORE:A}
  [[ ${d:h} == $store ]] && print -r -- ${d:t}
}

_cr_dir_is_managed() {
  local pat
  for pat in ${=CLAUDE_ROUTER_MANAGED_DIRS}; do
    [[ ${1:A} == ${~pat} ]] && return 0
  done
  return 1
}

# A CLAUDE_CONFIG_DIR that nobody chose: the default account that an interactive
# shell set at start-up (the managed zsh block exports CLAUDE_ROUTER_SHELL=1 there,
# and `claude-acs sw` clears it), or a dir another tool picked.
_cr_dir_is_auto() {
  local dir=$1 def
  _cr_dir_is_managed "$dir" && return 0
  [[ $CLAUDE_ROUTER_SHELL == 1 ]] || return 1
  def=$(_cr_default_account)
  [[ -n $def && ${dir:A} == ${CLAUDE_ACCOUNT_STORE:A}/$def ]]
}

# Lazily computed facts about the launch directory.
_cr_fact() {
  local key=$1 v=
  if (( ! ${+CR_FACTS[$key]} )); then
    case $key in
      path)   v=$CR_CWD ;;
      host)   v=${HOST:-$(hostname -s 2>/dev/null)} ;;
      remote) v=$(git -C "$CR_CWD" remote get-url origin 2>/dev/null) ;;
      branch) v=$(git -C "$CR_CWD" branch --show-current 2>/dev/null) ;;
      repo)
        v=$(_cr_fact remote); v=${${v%/}%.git}; v=${v##*[/:]}
        if [[ -z $v ]]; then   # no origin: the folder holding .git or .bare
          v=$(git -C "$CR_CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
          [[ -n $v ]] && v=${${v:h}:t}
        fi ;;
    esac
    CR_FACTS[$key]=$v
  fi
  print -r -- "${CR_FACTS[$key]}"
}

# One condition: key=glob, env:NAME=glob, either negated with a leading !.
# Globs are zsh patterns, and * also matches /. path= matches the folder or anything in it.
# remote, repo and host ignore case; path, branch and env do not.
_cr_cond() {
  local c=$1 neg=0 key pat val
  [[ $c == '!'* ]] && { neg=1; c=${c#!}; }
  [[ $c == *=* ]] || return 2
  key=${c%%=*} pat=${c#*=}
  case $key in
    env:[A-Za-z_]*)
      key=${key#env:}
      [[ $key == [A-Za-z_][A-Za-z0-9_]# ]] || return 2
      val=${(P)key} ;;
    path)
      [[ $pat == '~'* ]] && pat=$HOME${pat#'~'}
      pat=${pat%/}
      val=$(_cr_fact path)
      if [[ $val == ${~pat} || $val == ${~pat}/* ]]; then (( neg )) && return 1 || return 0; fi
      (( neg )) && return 0 || return 1 ;;
    remote|repo|host)   # case-insensitive, like GitHub owners and hostnames
      val=$(_cr_fact $key)
      if [[ $val == (#i)${~pat} ]]; then (( neg )) && return 1 || return 0; fi
      (( neg )) && return 0 || return 1 ;;
    branch) val=$(_cr_fact $key) ;;
    *) return 2 ;;
  esac
  if [[ $val == ${~pat} ]]; then (( neg )) && return 1 || return 0; fi
  (( neg )) && return 0 || return 1
}

# Rules file: `<account> [condition ...]`, all conditions must hold; a line with no
# conditions always matches (a catch-all). # starts a comment. Sets CR_RULE_ACCOUNT,
# CR_RULE_LINE. Returns 1 when nothing matched, 2 on a malformed line (CR_RULE_ERROR).
_cr_match_rules() {
  local file=$CLAUDE_ROUTER_RULES line acct n=0 rc c
  local -a words
  typeset -g CR_RULE_ACCOUNT= CR_RULE_LINE= CR_RULE_ERROR=
  [[ -r $file ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    (( n++ ))
    line=${line%%[[:space:]]#\#*}; [[ $line == \#* ]] && continue
    words=(${=line})
    (( $#words )) || continue
    acct=${words[1]}
    if ! _cr_valid_name "$acct"; then CR_RULE_ERROR="line $n: bad account name '$acct'"; return 2; fi
    rc=0
    for c in ${words[2,-1]}; do
      _cr_cond "$c"; rc=$?
      (( rc == 2 )) && { CR_RULE_ERROR="line $n: bad condition '$c'"; return 2; }
      (( rc )) && break
    done
    if (( rc == 0 )); then CR_RULE_ACCOUNT=$acct CR_RULE_LINE=$n; return 0; fi
  done < $file
  return 1
}

cr_decide() {
  CR_CWD=${1:-$PWD}; CR_CWD=${CR_CWD:A}
  CR_FACTS=()
  CR_ACCOUNT= CR_ACTION=keep CR_REASON=
  local dir=$CLAUDE_CONFIG_DIR acct pin def note=

  if [[ -n $CLAUDE_ROUTE_ACCOUNT ]]; then
    CR_ACCOUNT=$CLAUDE_ROUTE_ACCOUNT CR_ACTION=switch CR_REASON="CLAUDE_ROUTE_ACCOUNT"
    return 0
  fi

  if [[ -n $dir ]] && ! _cr_dir_is_auto "$dir"; then
    acct=$(_cr_account_of_dir "$dir")
    CR_ACCOUNT=$acct CR_ACTION=keep
    CR_REASON="already chosen: CLAUDE_CONFIG_DIR=${dir/#$HOME/~}"
    return 0
  fi
  [[ -n $dir ]] && _cr_dir_is_managed "$dir" && note=" (overrides ${dir/#$HOME/~})"

  pin=$(git -C "$CR_CWD" config --get claude.account 2>/dev/null)
  if [[ -n $pin ]]; then
    CR_ACCOUNT=$pin CR_ACTION=switch CR_REASON="repo pin (git config claude.account)$note"
    return 0
  fi

  _cr_match_rules; case $? in
    0) CR_ACCOUNT=$CR_RULE_ACCOUNT CR_ACTION=switch
       CR_REASON="rule line $CR_RULE_LINE in ${CLAUDE_ROUTER_RULES/#$HOME/~}$note"
       return 0 ;;
    2) CR_ACTION=error CR_REASON="rules file: $CR_RULE_ERROR"; return 2 ;;
  esac

  def=$(_cr_default_account)
  if [[ -n $dir ]] && ! _cr_dir_is_managed "$dir"; then
    CR_ACCOUNT=$(_cr_account_of_dir "$dir") CR_ACTION=keep CR_REASON="no rule matched: shell default"
  elif [[ -n $def ]]; then
    CR_ACCOUNT=$def CR_ACTION=switch CR_REASON="no rule matched: default account$note"
  else
    CR_ACCOUNT= CR_ACTION=keep CR_REASON="no rule matched, no default account: claude's own default"
  fi
  return 0
}

# Apply CR_ACCOUNT to this process's env the way `claude-acs switch` does (config dir
# plus that account's telemetry/privacy env). Fails closed: never falls back.
cr_apply() {
  [[ $CR_ACTION == switch ]] || return 0
  if ! _cr_account_exists "$CR_ACCOUNT"; then
    print -ru2 -- "claude-router: account '$CR_ACCOUNT' ($CR_REASON) is not saved under ${CLAUDE_ACCOUNT_STORE/#$HOME/~}; not starting claude"
    return 1
  fi
  if [[ ! -r $CLAUDE_ROUTER_TOGGLE ]]; then
    print -ru2 -- "claude-router: $CLAUDE_ROUTER_TOGGLE not found, cannot switch to '$CR_ACCOUNT' safely; not starting claude"
    return 1
  fi
  source "$CLAUDE_ROUTER_TOGGLE" >/dev/null 2>&1
  if ! claude-acs switch "$CR_ACCOUNT" >/dev/null 2>&1 \
     || [[ ${CLAUDE_CONFIG_DIR:A} != ${CLAUDE_ACCOUNT_STORE:A}/$CR_ACCOUNT ]]; then
    print -ru2 -- "claude-router: claude-acs switch $CR_ACCOUNT failed; not starting claude"
    return 1
  fi
  # The account is a choice now: a claude started from inside this one keeps it.
  unset CLAUDE_ROUTER_SHELL
}

# One line per launch; never the arguments (they can hold prompts).
cr_log() {
  local f=$CLAUDE_ROUTER_LOG ctx=
  [[ -n $ORCA_PANE_KEY ]] && ctx+=" orca"
  [[ -n $HERDR_SESSION ]] && ctx+=" herdr:$HERDR_SESSION"
  { mkdir -p "${f:h}" && print -r -- "$(date +%Y-%m-%dT%H:%M:%S%z) ${CR_ACCOUNT:--} $CR_ACTION cwd=${CR_CWD/#$HOME/~}${ctx} reason=$CR_REASON" >>| "$f"; } 2>/dev/null
  if [[ -f $f ]] && (( $(wc -c < "$f") > 524288 )); then
    { tail -n 2000 "$f" >| "$f.tmp" && mv -f "$f.tmp" "$f"; } 2>/dev/null
  fi
  return 0
}

# The real claude: CLAUDE_ROUTER_REAL_CLAUDE, else the first `claude` on PATH that is
# not this wrapper.
cr_real_claude() {
  local self=$1 d c
  if [[ -n $CLAUDE_ROUTER_REAL_CLAUDE ]]; then print -r -- $CLAUDE_ROUTER_REAL_CLAUDE; return 0; fi
  for d in $path; do
    c=$d/claude
    [[ -f $c && -x $c ]] || continue
    [[ ${c:A} == ${self:A} || ${d:A} == ${self:A:h} ]] && continue
    print -r -- $c; return 0
  done
  return 1
}
