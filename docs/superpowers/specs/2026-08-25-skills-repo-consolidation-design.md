# Skills Repo Consolidation — Design

**Date:** 2026-08-25
**Status:** Proposed, awaiting review
**Branch:** `feat/skills-consolidation`

## Problem

Three repositories each own part of the skill tree that `~/.claude/skills`
presents as one flat directory, so no single `git log` tells the truth about a
skill's history.

| Repo / path | Contents | Last touched | State |
|---|---|---|---|
| `acs-claude-skills` (= `~/.claude/skills`) | 85 entries, 72 real + 13 symlinks | live | **12 commits unpushed**, 13 modified, 4 untracked |
| `claude-bootstrap/.claude/skills` | 13 skills, symlinked into the above | 2026-08-22 | **7 commits unpushed**, dirty |
| `claude-bootstrap/skills` | 19 skills, wired to nothing | 2026-02-16 | stale |

All three accounts (`personal`, `personal2`, `harrison`) symlink
`skills -> ~/.claude/skills`, so there is exactly one tree and no cross-account
divergence. That part is sound and stays.

### The defect that matters

`acs-claude-skills` tracks the 13 bootstrap entries as git symlink objects
(mode `120000`) whose content is an **absolute path containing the username**:

```
120000 a676e30…  git-worktrees → /Users/alan.soewargo@harrison.ai/repos/claude-bootstrap/.claude/skills/git-worktrees/
```

Clone that repo on another machine, another user account, or a CI runner and you
get 13 dangling symlinks instead of skills. The repository is not portable, and
the failure is invisible on this machine because the path happens to resolve.

### Secondary defects

- **`claude-bootstrap/skills` (19) is dead.** Untouched since 2026-02-16. It
  duplicates 11 skills from `.claude/skills`, identical except `git-worktrees`,
  which has **drifted into two versions** — the stale copy is the one here.
- **`worktree-cleanup` is an untracked symlink** — owned by neither repo's index.
- **Both repos carry unpushed work.** Any migration that moves files before
  securing this risks losing it.

## Root cause: dependency direction

Bootstrap sits *upstream* of the daily-use tree, so it must inject itself via
`install.sh` symlinks. But `~/.claude/skills` is simultaneously a git working
tree, so `acs-claude-skills` tracks those foreign symlinks as if it owned them.
Two systems claim the same directory.

The asymmetry shows which way the dependency actually runs: those 13 skills were
edited two days ago, while bootstrap itself is **private and has not been pushed
since 2026-02-21**. They behave like personal skills, not a distributed product.

**Flipping the direction removes the need for symlinks entirely.** If the
personal repo is the source of truth and bootstrap becomes a curated *export*,
nothing links into anything.

```
BEFORE — bootstrap upstream            AFTER — personal repo upstream
  claude-bootstrap/.claude/skills/       ~/.claude/skills/git-worktrees
        |  install.sh symlinks                 (real files, owned outright)
        v                                            |
  ~/.claude/skills/git-worktrees                     |  copy out to publish
        (absolute-path symlink,                      v
         tracked by acs-claude-skills)         claude-bootstrap/.claude/skills/
```

## Non-goals

- **Not retiring `claude-bootstrap`.** It keeps its independent purpose as a
  shareable onboarding toolkit. It stops being an *input* to the daily tree.
- **Not changing the account symlink fan-out.** `personal`/`personal2`/
  `harrison` → `~/.claude/skills` stays exactly as is.
- **Not touching skill content.** This is a move-and-own migration. Any
  behavioural edit to a skill is separate work.

## Approach

### Phase 0 — Safety net (blocking; nothing else starts until this is done)

Both repos are dirty and unpushed. Migrating first would risk unrecoverable loss.

1. In `acs-claude-skills`: review and commit the 13 modified `turnstile-spin`
   files and decide on the 4 untracked entries (`sandbox-next`,
   `sandbox-stable`, `sandbox-migrate-to-next`, and the `worktree-cleanup`
   symlink). Push all 12+ commits.
2. In `claude-bootstrap`: commit the dirty `code-review`/`pr-code-review` edits
   and push its 7 commits.
3. Run `claude-acs backup run` and confirm a clean `verify`. This is the
   rollback of last resort — it captures symlinked skills **by content**, which
   was proven by restoring `git-worktrees` byte-identical.
4. Tag both repos (`pre-consolidation-2026-08-25`) so the prior state is
   addressable by name rather than by scrollback.

### Phase 1 — Rescue the 8 uniques

`claude-bootstrap/skills` holds 8 skills that exist nowhere else:
`python`, `typescript`, `react-web`, `nodejs-backend`, `playwright-testing`,
`codex-review`, `gemini-review`, `doc-coauthoring`.

They have been uninstalled and untouched for six months, so they are not
load-bearing — but deleting them before deciding would be irreversible in
practice. Move them to `claude-bootstrap/.claude/skills/` (the live directory)
**or** archive them, per file-by-file judgement. Decide before Phase 3.

### Phase 2 — Absorb the 13

For each of the 13 symlinks in `~/.claude/skills`, replace the link with the real
directory contents and commit them to `acs-claude-skills`.

Ordering matters: git records a **type change** (symlink → directory), so each
must be removed from the index and re-added, not merely overwritten.

```
git rm --cached <name>          # drop the 120000 symlink object
rm <name>                       # remove the link itself
cp -R <bootstrap>/<name> <name> # real content, dereferenced
git add <name>
```

`worktree-cleanup` is untracked, so it needs the same treatment minus the
`git rm --cached`.

Verification per skill: the copied tree must be byte-identical to the symlink
target before the link is destroyed.

### Phase 3 — Retire the stale 19

Delete `claude-bootstrap/skills/` once Phase 1 has rescued the 8 uniques. This
also removes the drifted duplicate `git-worktrees`, leaving one version.

Git history retains everything, so this is recoverable.

### Phase 4 — Reverse the tooling

`install.sh` currently symlinks `.claude/skills/*` into `~/.claude/skills`. After
Phase 2 it would find real directories and skip them with a warning — harmless
but misleading.

- Update `install.sh` so it no longer targets skills already owned by
  `acs-claude-skills`, or gate it behind an explicit flag for fresh machines.
- The `bootstrap-sync` skill (itself one of the 13 being absorbed) currently
  syncs bootstrap → `~/.claude`. Its purpose inverts: it should either be
  retired or rewritten as a **publish** step (personal repo → bootstrap).
- Document the new direction in `claude-bootstrap/README.md`.

### Phase 5 — Verification

1. `~/.claude/skills` contains **zero symlinks**:
   `find ~/.claude/skills -maxdepth 1 -type l` returns nothing.
2. `git ls-files -s | grep ^120000` in `acs-claude-skills` returns nothing.
3. Skill count unchanged at 85; every skill still loads in a new session.
4. `claude-acs backup run` reports `0 new` — content is identical, so absorbing
   must not fabricate snapshots. **This is the strongest single check**: it
   proves the migration moved ownership without altering a byte.
5. Clone `acs-claude-skills` to a temp dir and confirm all 85 skills are real
   files — the portability defect that motivated the work.

## Risks

| Risk | Mitigation |
|---|---|
| Unpushed work lost during the move | Phase 0 blocks everything; both repos pushed and tagged first |
| Copy captures the symlink, not content | Verify byte-identical before removing each link; `find -type l` check at the end |
| Skill silently disappears from Claude Code | Count check (85) plus a live session load after migration |
| Absorbing changes content and churns the backup | Backup `0 new` check in Phase 5 |
| `install.sh` re-creates symlinks on a later run | Phase 4 updates it; until then it skips real dirs rather than clobbering |
| The 8 uniques deleted while still wanted | Phase 1 precedes Phase 3; git history retains them regardless |

## Open questions for review

1. **The 4 untracked entries** in `~/.claude/skills` — are `sandbox-next`,
   `sandbox-stable` and `sandbox-migrate-to-next` intended as permanent skills,
   or scratch? They must be committed or removed before Phase 2.
2. **The 8 uniques** — promote to bootstrap's live directory, or archive? They
   look like language/framework starters (`python`, `typescript`, `react-web`)
   that may be superseded by plugins already installed.
3. **Does `bootstrap-sync` survive?** If bootstrap becomes an export target, a
   publish command is useful; if publishing stays manual and rare, the skill is
   dead weight.
4. **Should `acs-claude-skills` become private-safe?** It holds work-specific
   skills (`ask-pensieve`, `v2-support-roster`, `jira-ticket-writer`). Worth
   confirming its visibility is deliberate before it becomes the sole source of
   truth.
