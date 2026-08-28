# Claude Code Model + Account Toggle
# Source this in ~/.zshrc:
#   echo "source ~/claude_code_toggle.sh" >> ~/.zshrc

# ─── Config ───────────────────────────────────────────────────────────────────
CLAUDE_ACCOUNT_STORE="$HOME/.claude-accounts"  # per-account config dirs live here
CLAUDE_MODE="remote"
CLAUDE_ACCOUNT="default"
CLAUDE_DEFAULT_FILE="$CLAUDE_ACCOUNT_STORE/.default"

# Space-separated list of account names that default to telemetry ON.
# Anything not in this list defaults to telemetry OFF when switched to.
CLAUDE_OTEL_ORG_ACCOUNTS="${CLAUDE_OTEL_ORG_ACCOUNTS:-harrison}"

# ─── Name validation ─────────────────────────────────────────────────────────

__claude_acs_validate_name() {
  local name="$1"
  if [ -z "$name" ]; then
    echo "❌ Account name cannot be empty"
    return 1
  fi
  if ! printf '%s' "$name" | grep -qE '^[a-zA-Z0-9._-]+$'; then
    echo "❌ Invalid account name '$name'"
    echo "   Allowed: letters, digits, hyphens, underscores, dots"
    return 1
  fi
  return 0
}

# ─── Telemetry (OpenTelemetry) helpers ──────────────────────────────────────

_claude_otel_is_org_account() {
  local name="$1" org
  for org in $CLAUDE_OTEL_ORG_ACCOUNTS; do
    [ "$name" = "$org" ] && return 0
  done
  return 1
}

# List names of OTEL-related env vars currently set in this shell.
_claude_otel_list_vars() {
  env | awk -F= '
    /^OTEL_[A-Z_]+=/                      { print $1; next }
    /^CLAUDE_CODE_ENABLE_TELEMETRY=/      { print $1; next }
    /^CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=/ { print $1 }
  '
}

# Snapshot current OTEL vars into a single shell variable (serialised export lines).
# Idempotent: if a snapshot already exists, keep it (so repeat disables don't clobber it).
_claude_otel_snapshot() {
  [ -n "$_CLAUDE_OTEL_BACKUP" ] && return 0
  local blob="" var val val_esc
  for var in $(_claude_otel_list_vars); do
    val="$(printenv "$var")"
    val_esc="$(printf '%s' "$val" | sed "s/'/'\\\\''/g")"
    blob="${blob}export ${var}='${val_esc}'"$'\n'
  done
  _CLAUDE_OTEL_BACKUP="$blob"
}

_claude_otel_disable() {
  _claude_otel_snapshot
  local var
  for var in $(_claude_otel_list_vars); do
    unset "$var"
  done
  export OTEL_SDK_DISABLED=true
  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
}

_claude_otel_enable() {
  unset OTEL_SDK_DISABLED
  unset CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
  if [ -n "$_CLAUDE_OTEL_BACKUP" ]; then
    eval "$_CLAUDE_OTEL_BACKUP"
    unset _CLAUDE_OTEL_BACKUP
  fi
}

# Apply the per-account default: enable on org accounts, disable on everything else.
_claude_otel_apply_account_default() {
  local name="$1"
  if _claude_otel_is_org_account "$name"; then
    _claude_otel_enable
  else
    _claude_otel_disable
  fi
}

_claude_otel_is_enabled() {
  [ -n "$OTEL_EXPORTER_OTLP_ENDPOINT" ] && [ -z "$OTEL_SDK_DISABLED" ]
}

# ─── Auto-switch to default account on shell start (silent) ──────────────────
if [ -f "$CLAUDE_DEFAULT_FILE" ]; then
  _claude_default_account="$(cat "$CLAUDE_DEFAULT_FILE")"
  if [ -d "$CLAUDE_ACCOUNT_STORE/$_claude_default_account" ]; then
    export CLAUDE_CONFIG_DIR="$CLAUDE_ACCOUNT_STORE/$_claude_default_account"
    CLAUDE_ACCOUNT="$_claude_default_account"
    _claude_otel_apply_account_default "$_claude_default_account"
  else
    echo "⚠️  Default account '$_claude_default_account' not found — falling back to ~/.claude"
    echo "   Run: claude-acs df --clear  or  claude-acs save $_claude_default_account"
  fi
  unset _claude_default_account
fi

# ─── claude-acs: unified account management ──────────────────────────────────

claude-acs() {
  local cmd="${1:-help}"
  shift 2>/dev/null

  case "$cmd" in
    switch|sw)
      __claude_acs_switch "$@"
      ;;
    save|sv)
      __claude_acs_save "$@"
      ;;
    remove|rm)
      __claude_acs_remove "$@"
      ;;
    list|ls)
      __claude_acs_list "$@"
      ;;
    default|df)
      __claude_acs_default "$@"
      ;;
    status|st)
      __claude_acs_status "$@"
      ;;
    telemetry|tel)
      __claude_acs_telemetry "$@"
      ;;
    apply-profile|ap)
      __claude_acs_apply_profile "$@"
      ;;
    stats|usage)
      __claude_acs_stats "$@"
      ;;
    backup|bk)
      __claude_acs_backup "$@"
      ;;
    skills|sk)
      __claude_acs_skills "$@"
      ;;
    help|--help|-h)
      __claude_acs_help "$@"
      ;;
    *)
      echo "❌ Unknown command: $cmd"
      echo ""
      __claude_acs_help
      return 1
      ;;
  esac
}

__claude_acs_help() {
  cat <<'HELP'
┌─ claude-acs ─────────────────────────────────────────────────────────┐
│  Manage Claude Code accounts and sessions                            │
│                                                                      │
│  USAGE                                                               │
│    claude-acs <command> [args]                                        │
│                                                                      │
│  COMMANDS                                                            │
│    switch, sw  <name>       Switch active account for this shell     │
│    save,   sv  <name>       Snapshot current login as a named acct   │
│    remove, rm  <name>       Delete a saved account                   │
│    list,   ls               List all saved accounts                  │
│    default, df [name]       Show/set/clear the startup default       │
│    status, st               Show full Claude Code environment info   │
│    telemetry, tel [on|off]  Show/force OpenTelemetry state           │
│    apply-profile, ap <acct>  Apply lean plugin profile to account    │
│    stats, usage             Cross-account usage stats + dashboard    │
│    backup, bk  [sub]        SKILL/CONFIG BACKUP — versioned, to      │
│                             OneDrive. NOT the same as `save`, which  │
│                             snapshots an account login.              │
│                             subs: run status list restore prune      │
│                                   verify install-schedule            │
│    skills, sk  [sub]        Keep ~/.claude/skills current with its   │
│                             remote. PULL ONLY — refuses when dirty   │
│                             or diverged, never stashes or resets.    │
│                             subs: pull status install-schedule       │
│    help,   -h               Show this help                           │
│                                                                      │
│  EXAMPLES                                                            │
│    claude-acs switch personal    # use personal account this shell   │
│    claude-acs sw work            # shorthand                         │
│    claude-acs default personal   # auto-switch on every new shell    │
│    claude-acs df --clear         # stop auto-switching               │
│    claude-acs save myteam        # snapshot current login            │
│    claude-acs ls                 # see what's saved                  │
│    claude-acs st                 # full status dump                  │
│    claude-acs tel off            # disable OTEL even on org account  │
│    claude-acs tel on             # re-enable OTEL (restore snapshot) │
│    claude-acs backup run         # snapshot changed skills/config    │
│    claude-acs bk status          # last backup run + store size      │
│    claude-acs bk restore my-skill --at 2026-07-01                    │
│                                                                      │
│  QUICK START                                                         │
│    1. Log in:    claude → /login                                     │
│    2. Save:      claude-acs save personal                            │
│    3. Set default: claude-acs default personal                       │
│    4. Repeat for other accounts (work, team, etc.)                   │
│    5. Switch:    claude-acs sw work                                  │
└──────────────────────────────────────────────────────────────────────┘
HELP
}

# Resolve a setup script inside the acs-agentic-setup repo.
# Handles BOTH layouts: a normal checkout ($repo/setups/...) and the bare-repo
# worktree pattern ($repo/<branch-worktree>/setups/...), which is what
# `git-worktrees bare-init` leaves behind. Without the worktree fallback every
# wrapper here breaks the moment the repo is converted.
# Usage: __claude_acs_setup_script <relative-path-under-setups>
# Echoes the resolved path and returns 0, or echoes nothing and returns 1.
__claude_acs_setup_script() {
  # zsh errors on an unmatched glob by default; null_glob makes it expand to
  # nothing so the not-found path stays quiet. Scoped to this function.
  if [ -n "$ZSH_VERSION" ]; then
    setopt local_options null_glob
  fi
  local rel="$1"
  local repo="${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
  # 1. Normal checkout.
  if [ -f "$repo/setups/$rel" ]; then
    printf '%s\n' "$repo/setups/$rel"
    return 0
  fi
  # 2. Bare-repo worktree pattern: scan sibling worktrees, sorted for determinism.
  local candidate
  for candidate in "$repo"/*/setups/"$rel"; do
    [ -f "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

# Apply the shared lean-core plugin profile (from the acs-agentic-setup repo)
# to an account's settings.json. Thin wrapper — all logic lives in the repo.
# Override the repo location with CLAUDE_ACS_PROFILES_REPO.
__claude_acs_apply_profile() {
  local name="${1:?Usage: claude-acs apply-profile <account> [--dry-run] [--yes] [--prune-skills]}"
  __claude_acs_validate_name "$name" || return 1
  shift
  local script
  script="$(__claude_acs_setup_script claude-code-account-profiles/scripts/apply_profile.py)"
  if [ -z "$script" ]; then
    echo "❌ profile script not found under ${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
    echo "   Looked in setups/ and in each worktree's setups/."
    echo "   Set CLAUDE_ACS_REPO to your acs-agentic-setup checkout or worktree."
    return 1
  fi
  python3 "$script" --account "$name" --store "$CLAUDE_ACCOUNT_STORE" "$@"
}

# Cross-account usage statistics + HTML dashboard (from the acs-agentic-setup
# repo). Thin wrapper — all logic lives in the repo. Override the repo location
# with CLAUDE_ACS_REPO (or the older CLAUDE_ACS_PROFILES_REPO, kept working for
# both setups).
__claude_acs_stats() {
  local script
  script="$(__claude_acs_setup_script claude-code-usage-stats/scripts/collect_stats.py)"
  if [ -z "$script" ]; then
    echo "❌ stats script not found under ${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
    echo "   Looked in setups/ and in each worktree's setups/."
    echo "   Set CLAUDE_ACS_REPO to your acs-agentic-setup checkout or worktree."
    return 1
  fi
  python3 "$script" --store "$CLAUDE_ACCOUNT_STORE" "$@"
}

# Versioned, deduplicated backup of hand-authored Claude config — skills,
# commands, agents, hooks, per-account settings, plugin manifests. Snapshots only
# what changed, prunes on an age-tiered retention policy, stores blobs in
# OneDrive. Thin wrapper — all logic lives in the acs-agentic-setup repo.
#
# NOT to be confused with `claude-acs save`, which snapshots an ACCOUNT LOGIN.
# This one backs up SKILLS AND CONFIG. Different subsystems, similar verbs.
__claude_acs_backup() {
  local script
  script="$(__claude_acs_setup_script claude-skills-backup/scripts/backup.py)"
  if [ -z "$script" ]; then
    echo "❌ backup script not found under ${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
    echo "   Looked in setups/ and in each worktree's setups/."
    echo "   Set CLAUDE_ACS_REPO to your acs-agentic-setup checkout or worktree."
    return 1
  fi
  python3 "$script" "$@"
}

# Keep ~/.claude/skills current with the acs-claude-skills remote. PULL ONLY:
# fast-forwards when the tree is clean, and refuses -- never stashes, resets or
# merges -- when it is dirty or diverged. Committing and pushing stay manual, so
# skill history keeps meaningful messages. Thin wrapper; logic lives in the repo.
__claude_acs_skills() {
  local script
  script="$(__claude_acs_setup_script claude-skills-sync/scripts/skills_sync.py)"
  if [ -z "$script" ]; then
    echo "❌ skills sync script not found under ${CLAUDE_ACS_REPO:-${CLAUDE_ACS_PROFILES_REPO:-$HOME/repos/acs-agentic-setup}}"
    echo "   Looked in setups/ and in each worktree's setups/."
    echo "   Set CLAUDE_ACS_REPO to your acs-agentic-setup checkout or worktree."
    return 1
  fi
  python3 "$script" "$@"
}

__claude_acs_switch() {
  local name="${1:?Usage: claude-acs switch <account-name>}"
  __claude_acs_validate_name "$name" || return 1
  local target="$CLAUDE_ACCOUNT_STORE/$name"

  if [ ! -d "$target" ]; then
    echo "❌ No saved account '$name'"
    echo "   Run: claude → /login  →  claude-acs save $name"
    return 1
  fi

  export CLAUDE_CONFIG_DIR="$target"
  CLAUDE_ACCOUNT="$name"
  _claude_otel_apply_account_default "$name"
  echo "✅ Switched to account: $name"
  echo "   CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"
  if _claude_otel_is_enabled; then
    echo "   📡 Telemetry: ENABLED (org default)"
  else
    echo "   🔕 Telemetry: DISABLED"
  fi
  echo "   New 'claude' instances in this shell will use: $name"
}

__claude_acs_save() {
  local name="${1:?Usage: claude-acs save <account-name>}"
  __claude_acs_validate_name "$name" || return 1
  local src="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  local dest="$CLAUDE_ACCOUNT_STORE/$name"

  if [ ! -d "$src" ]; then
    echo "❌ No config dir found at $src"
    echo "   Make sure you're logged in: claude → /login"
    return 1
  fi

  mkdir -p "$dest"

  # Shared dirs are symlinked, not copied, so all accounts stay in sync.
  # Add directory names here to share them across accounts.
  # plugins is shared too (canonical: ~/.claude/plugins) so installs are global
  # across all accounts — see ~/.claude-accounts/README.md.
  local shared_dirs="skills commands plugins"

  # Copy everything except shared dirs
  for item in "$src"/{.*,*}; do
    local base="$(basename "$item")"
    [ "$base" = "." ] || [ "$base" = ".." ] && continue
    # Skip shared dirs — they get symlinked below
    local skip=0
    for sd in $shared_dirs; do
      [ "$base" = "$sd" ] && skip=1 && break
    done
    [ "$skip" -eq 1 ] && continue
    cp -r "$item" "$dest/" 2>/dev/null
  done

  # Create symlinks for shared dirs (idempotent)
  for sd in $shared_dirs; do
    if [ -d "$src/$sd" ] || [ -L "$src/$sd" ]; then
      # Resolve to the canonical source (follow symlinks in src)
      local real_src="$src/$sd"
      [ -L "$real_src" ] && real_src="$(readlink "$real_src")"
      # Remove existing copy, replace with symlink
      [ -e "$dest/$sd" ] && [ ! -L "$dest/$sd" ] && rm -rf "$dest/$sd"
      [ ! -L "$dest/$sd" ] && ln -s "$real_src" "$dest/$sd"
    fi
  done

  chmod -R 700 "$dest" 2>/dev/null
  echo "💾 Saved account '$name' from $src"
  echo "   Shared (symlinked): $shared_dirs"
}

__claude_acs_remove() {
  local name="${1:?Usage: claude-acs remove <account-name>}"
  __claude_acs_validate_name "$name" || return 1
  local target="$CLAUDE_ACCOUNT_STORE/$name"

  if [ ! -d "$target" ]; then
    echo "❌ No saved account '$name'"
    return 1
  fi

  if [ "$name" = "$CLAUDE_ACCOUNT" ]; then
    echo "⚠️  '$name' is the currently active account — switch away first"
    return 1
  fi

  rm -rf "$target"
  echo "🗑️  Removed account: $name"
}

__claude_acs_list() {
  echo "Saved accounts in $CLAUDE_ACCOUNT_STORE:"
  local found=0 acct="" flags=""
  local default_name=""
  [ -f "$CLAUDE_DEFAULT_FILE" ] && default_name="$(cat "$CLAUDE_DEFAULT_FILE")"

  for d in "$CLAUDE_ACCOUNT_STORE"/*/; do
    [ -d "$d" ] || continue
    acct="$(basename "$d")"
    flags=""
    [ "$acct" = "$CLAUDE_ACCOUNT" ] && flags="${flags} ← active"
    [ "$acct" = "$default_name" ] && flags="${flags} (default)"
    if [ -n "$flags" ]; then
      echo "  ✅ $acct$flags"
    else
      echo "     $acct"
    fi
    found=1
  done
  if [ "$found" -eq 0 ]; then
    echo "  (none — run 'claude → /login' then 'claude-acs save <name>')"
  fi
}

__claude_acs_default() {
  local name="$1"

  if [ -z "$name" ]; then
    if [ -f "$CLAUDE_DEFAULT_FILE" ]; then
      echo "Default account: $(cat "$CLAUDE_DEFAULT_FILE")"
    else
      echo "No default account set (using ~/.claude)"
    fi
    return 0
  fi

  if [ "$name" = "--clear" ]; then
    rm -f "$CLAUDE_DEFAULT_FILE"
    echo "🧹 Default account cleared — new shells will use ~/.claude"
    return 0
  fi

  __claude_acs_validate_name "$name" || return 1

  if [ ! -d "$CLAUDE_ACCOUNT_STORE/$name" ]; then
    echo "❌ No saved account '$name'"
    echo "   Run: claude → /login  →  claude-acs save $name"
    return 1
  fi

  echo "$name" > "$CLAUDE_DEFAULT_FILE"
  echo "📌 Default account set to: $name"
  echo "   New shells will auto-switch to '$name'"
}

__claude_acs_telemetry() {
  local action="${1:-status}"
  case "$action" in
    on|enable)
      _claude_otel_enable
      if _claude_otel_is_enabled; then
        echo "📡 Telemetry ENABLED → $OTEL_EXPORTER_OTLP_ENDPOINT"
        echo "   Resource: ${OTEL_RESOURCE_ATTRIBUTES:-(none)}"
      else
        echo "⚠️  Could not enable — no OTEL snapshot in this shell."
        echo "   Open a new shell (which inherits fresh OTEL vars from iTerm2/MDM)"
        echo "   and switch to an org account, or set OTEL_* manually."
      fi
      ;;
    off|disable)
      _claude_otel_disable
      echo "🔕 Telemetry DISABLED for this shell"
      ;;
    status|st|"")
      if _claude_otel_is_enabled; then
        echo "📡 Telemetry: ENABLED"
        echo "   Endpoint: $OTEL_EXPORTER_OTLP_ENDPOINT"
        echo "   Resource: ${OTEL_RESOURCE_ATTRIBUTES:-(none)}"
      else
        echo "🔕 Telemetry: DISABLED"
        [ -n "$_CLAUDE_OTEL_BACKUP" ] && echo "   (snapshot held — 'claude-acs tel on' will restore)"
      fi
      ;;
    *)
      echo "❌ Unknown telemetry action: $action"
      echo "   Usage: claude-acs telemetry [on|off|status]"
      return 1
      ;;
  esac
}

__claude_acs_status() {
  local config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  local creds_file="$config_dir/.credentials.json"
  local sub_type="" expires_at="" live_email=""
  local default_name=""
  [ -f "$CLAUDE_DEFAULT_FILE" ] && default_name="$(cat "$CLAUDE_DEFAULT_FILE")"

  if [ -f "$creds_file" ]; then
    local _creds_output
    _creds_output=$(python3 -c "
import json,sys,datetime
with open(sys.argv[1]) as f:
    d=json.load(f)
o=d.get('claudeAiOauth',{})
email=o.get('emailAddress','?')
sub=o.get('subscriptionType') or 'enterprise/team'
ts=o.get('expiresAt',0)
exp=datetime.datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M') if ts else '?'
print(email)
print(sub)
print(exp)
" "$creds_file" 2>/dev/null)
    if [ -n "$_creds_output" ]; then
      live_email=$(echo "$_creds_output" | sed -n '1p')
      sub_type=$(echo "$_creds_output" | sed -n '2p')
      expires_at=$(echo "$_creds_output" | sed -n '3p')
    fi
  fi

  echo "┌─ Claude Code Status ────────────────────────────────"
  echo "│  Mode:            $CLAUDE_MODE"
  echo "│  Account (active): $CLAUDE_ACCOUNT"
  echo "│  Account (default): ${default_name:-(none)}"
  echo "│  CLAUDE_CONFIG_DIR: ${CLAUDE_CONFIG_DIR:-"(unset — using ~/.claude)"}"
  echo "│  Creds file:      ${creds_file} $([ -f "$creds_file" ] && echo '✅' || echo '❌')"
  echo "│  Email:           ${live_email:-(unknown)}"
  echo "│  Subscription:    ${sub_type:-(unknown)}"
  echo "│  Token expires:   ${expires_at:-(unknown)}"
  echo "│  ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL:-"(unset — Anthropic default)"}"
  echo "│  ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:+"set"}${ANTHROPIC_API_KEY:-"(unset)"}"
  echo "│  ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-"(unset — Claude Code default)"}"
  if _claude_otel_is_enabled; then
    echo "│  Telemetry:      📡 ENABLED → ${OTEL_EXPORTER_OTLP_ENDPOINT}"
    echo "│                   resource: ${OTEL_RESOURCE_ATTRIBUTES:-(none)}"
  else
    echo "│  Telemetry:      🔕 DISABLED${_CLAUDE_OTEL_BACKUP:+ (snapshot held)}"
  fi
  echo "└─────────────────────────────────────────────────────"
}

# ─── Legacy aliases (backwards compat) ───────────────────────────────────────
claude-account-switch() { claude-acs switch "$@"; }
claude-account-save()   { claude-acs save "$@"; }
claude-account-remove() { claude-acs remove "$@"; }
claude-account-list()   { claude-acs list "$@"; }
claude-account-default(){ claude-acs default "$@"; }
claude-status()         { claude-acs status "$@"; }

# ─── Model / endpoint toggle ────────────────────────────────────────────────

claude-local() {
  local model="${1:-qwen3-coder:30b}"
  export ANTHROPIC_BASE_URL="http://localhost:11434"
  export ANTHROPIC_AUTH_TOKEN="ollama"
  export ANTHROPIC_MODEL="$model"
  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
  unset ANTHROPIC_API_KEY
  CLAUDE_MODE="local"
  echo "✅ Claude Code → LOCAL (Ollama) — model: $model"
}

claude-remote() {
  unset ANTHROPIC_BASE_URL
  unset ANTHROPIC_AUTH_TOKEN
  unset ANTHROPIC_MODEL
  unset CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
  CLAUDE_MODE="remote"
  echo "✅ Claude Code → REMOTE (Anthropic) — account: $CLAUDE_ACCOUNT"
}

claude-toggle() {
  if [ "$CLAUDE_MODE" = "local" ]; then claude-remote; else claude-local; fi
}
