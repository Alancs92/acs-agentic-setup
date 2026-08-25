# claude-skills-sync

Keeps this machine's `~/.claude/skills` current with the `acs-claude-skills`
remote, on a daily schedule, **without ever being able to lose local work**.

```
claude-acs skills pull
```

> **This is inbound sync.** It is unrelated to `claude-acs backup`, which
> versions and archives skill *content*, and to `claude-acs save`, which saves an
> *account profile*. Three neighbouring subsystems, three different jobs.

---

## LEGEND

Read this before the code. Every term below means exactly one thing throughout
this setup.

| Term | Meaning |
|---|---|
| **target** | The one repository this setup is permitted to touch: `config.target.path`, currently `~/.claude/skills`. Anything else is refused, loudly. |
| **identity gate** | The check that the target is a git work tree, is *its own* repository root, and has the configured remote. Runs before every command; a failure means nothing else runs. |
| **dirty** | The working tree has modified **or** untracked files. Not an error — the expected daily state for someone who writes skills. A dirty target is skipped, never resolved. |
| **diverged** | Local has commits the remote lacks **and** the remote has commits local lacks. Requires a human. This tool will not merge, rebase or choose a side. |
| **fast-forward** | The only mutating operation available: `git merge --ff-only`, which by construction cannot create a merge commit, rewrite history, or succeed at all unless HEAD is already an ancestor of the remote ref. |
| **outcome** | The single word each run concludes with, from a closed set (`fast-forwarded`, `up-to-date`, `skipped-dirty`, `skipped-diverged`, …). One per run, always logged. |
| **run log** | `~/Library/Logs/com.acs.claude-skills-sync.log`. Append-only, one timestamped line per run. Also launchd's `StandardOutPath`, so it may contain the odd traceback between structured lines. |
| **attention** | The subset of outcomes needing a human: diverged, refused, merge-failed. `status` exits `2` for these; a briefing script can branch on it. |
| **stale** | No successful sync recorded within `policy.stale_after_days` (14). The tripwire for "the launchd job quietly stopped loading". |

---

## Why this exists, and why it is so cautious

`~/.claude/skills` wears three hats at once. It is a git working tree (the
`acs-claude-skills` repo). It is the directory Claude Code reads skills from at
session start. And it is where a half-finished skill is being edited *right
now*.

That third hat is the whole problem. An unattended sync job pointed at this
directory is holding the only copy of whatever the user was mid-way through
writing, and it runs when nobody is watching. The ordinary tools for "make my
checkout match the remote" — `stash`, `reset --hard`, `clean`, `checkout --`,
`pull --rebase` — all assume the working tree is disposable. Here it is not.

So this tool has exactly one capability: fast-forward a completely clean tree.
Every other state is logged and left alone. That is not a default; there is no
flag, config key, or environment variable that widens it.

### How that is enforced rather than intended

`scripts/gitops.py` is the only module allowed to invoke git, and it holds an
**allow-list** of subcommands and, per subcommand, of flags. A deny-list would
fail open — the next person adds `git restore`, nobody listed it, and a night's
work is gone silently. The allow-list fails closed: a verb nobody has thought
about is refused by default.

The consequence, stated plainly: `reset`, `clean`, `stash`, `checkout`,
`switch`, `restore`, `rebase`, `pull`, `push` and every `--force` variant are
**not reachable from this code**. Adding one means editing a table with a
comment explaining why it is there, which is the right amount of friction for
that decision.

A test greps the package's own AST and fails if any of those tokens appears as
a string constant in a production module, if any module other than `gitops.py`
so much as names the `git` binary, or if `shell=True` or `os.system` appears
anywhere.

## How `pull` decides

Six gates, in order. Each is a hard stop.

| # | Gate | If it fails |
|---|---|---|
| 1 | Target is a git work tree **and its own root** | refuse, exit `1` |
| 2 | `origin` matches `config.target.remote` | refuse, exit `1` |
| 3 | `git fetch` succeeds | report, exit `1`, repo untouched |
| 4 | Working tree is clean (no modified, no untracked) | **skip, exit `0`** |
| 5 | Not diverged | skip **loudly**, exit `2` |
| 6 | Clean and strictly behind → `git merge --ff-only` | — |

**Gate 4 exits 0 on purpose.** A dirty tree is the normal case, not a fault, and
a job that alarms on the normal case gets muted — and a muted job is not a job.
Gate 5 is the one that shouts, because divergence genuinely needs a person.

Gate 1's "and its own root" is not decoration. If `~/.claude` were ever a git
repo while `~/.claude/skills` was a plain directory inside it, `git rev-parse`
would answer happily from the parent and every later check — remote, branch,
dirtiness, ahead/behind — would be describing a repository nobody pointed us at.

Detached HEAD, a branch other than the configured one, and a missing
remote-tracking ref are all additional skips. The rule throughout: a state this
tool does not completely understand ends with nothing changed.

Between gate 3 and gate 4 the repository state is re-read, because the fetch
moved refs and any ahead/behind figures from before it are stale.

## Commands

```
claude-acs skills pull [--quiet]            fast-forward if, and only if, it is safe
claude-acs skills status [--fetch]          one glanceable block; exit code == alarm
claude-acs skills install-schedule [--print-only]
```

`status` is built for a morning-briefing script:

```
repo       /Users/…/.claude/skills
branch     main
dirty      0 modified, 0 untracked
unpushed   0 commit(s)
behind     0 commit(s)
last sync  2026-08-25T07:15:02Z  (0.3 days ago)
status     ok
```

| Exit | Meaning |
|---|---|
| `0` | Fine. Includes a dirty tree and unpushed commits — both are normal. |
| `1` | Refused: wrong repository, or not a repository. |
| `2` | Attention: diverged, or no successful sync inside `stale_after_days`. |

`status` does **not** fetch by default. It is meant to be called at 07:45 by a
briefing that must not hang on a network round-trip; the numbers are as fresh as
the 07:15 scheduled pull. Pass `--fetch` when accuracy beats latency.

## Scheduling

`install-schedule` writes and loads `~/Library/LaunchAgents/com.acs.claude-skills-sync.plist`,
following the same conventions as the backup job and `com.acs.tide-sync.plist`:
explicit `ProgramArguments`, `EnvironmentVariables` with `HOME` and a spelled-out
`PATH`, `RunAtLoad` false, `StandardOutPath == StandardErrorPath`, `ProcessType`
Background. Cadence has one source of truth: `config.json`.

**Daily**, not weekly like the backup job. A pull is cheap, does nothing when
there is nothing to pull, and is a no-op whenever the tree is dirty — its cost is
bounded and its value decays with staleness, which is the opposite of a backup's
profile. 07:15 puts it just ahead of the 07:45 morning briefing.

Two details that are easy to get wrong and expensive to debug:

- **The log must live under `~/Library/Logs`.** launchd does not create a missing
  `StandardOutPath` directory — it silently declines to spawn the job, with no
  error anywhere except a bare status in `launchctl print`. `~/Library/Logs`
  always exists. `~/.claude/logs`, the obvious-looking choice, does not on a
  fresh machine, and is exactly the trap.
- **The job runs `pull --quiet`.** The structured line already goes to the log
  file, which is the same file launchd captures stdout into; without `--quiet`
  every run would write it twice. `StandardOutPath` then carries only the things
  nobody planned for, which is what makes tailing it useful.

## Run log

```
2026-08-25T07:15:02Z  pull  fast-forwarded  branch=main commits=3 from=a1b2c3d4e5f6 to=f6e5d4c3b2a1
2026-08-26T07:15:01Z  pull  skipped-dirty   branch=main modified=2 untracked=1 behind=1
2026-08-27T07:15:03Z  pull  skipped-diverged branch=main ahead=1 behind=2
```

A job that mostly does nothing is indistinguishable from a job that is not
running at all — unless it says so. Most runs here are expected to end in
`skipped-dirty`, so "nothing happened" is recorded as loudly as "something
happened"; otherwise `status` could not tell "up to date" from "the launchd
agent was never loaded".

| Outcome | Exit | Meaning |
|---|---|---|
| `fast-forwarded` | 0 | Clean and behind; HEAD advanced. |
| `up-to-date` | 0 | Nothing to pull. Counts as a successful sync. |
| `skipped-dirty` | 0 | Modified or untracked files present. The common case. |
| `skipped-ahead` | 0 | Unpushed commits, nothing incoming. |
| `skipped-detached-head` | 0 | HEAD is not on a branch. |
| `skipped-other-branch` | 0 | On a branch other than the configured one. |
| `skipped-no-upstream` | 0 | No remote-tracking ref even after fetching. |
| `skipped-diverged` | 2 | Both sides have commits the other lacks. **Human required.** |
| `refused-not-a-repo` | 1 | Target is not a work tree, or not its own root. |
| `refused-remote-mismatch` | 1 | `origin` is not the configured remote. |
| `fetch-failed` | 1 | Offline, or the remote is gone. Repo untouched. |
| `merge-failed` | 2 | git declined the fast-forward. No fallback is attempted. |

## Configuration

Everything tunable lives in `config.json`. Only one key changes behaviour —
`policy.stale_after_days` — and it only changes `status`'s exit code, never what
`pull` is willing to do.

`target.remote` is compared against the target's actual `origin` before anything
runs. Comparison normalises `https://`, `ssh://`, the scp-like
`git@host:owner/repo`, an optional port, and an optional `.git` suffix, so
re-cloning over SSH does not trip the gate while a genuinely different
repository still does.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Laptop offline / remote unreachable | `fetch-failed`, exit 1, repository untouched |
| Credential prompt under launchd | `GIT_TERMINAL_PROMPT=0` makes it fail rather than hang; a 120s timeout backs that up |
| Dirty tree | Skipped, exit 0, every byte left in place |
| Diverged | Skipped, exit 2, loud message, no merge commit created |
| Someone re-points `~/.claude/skills` at another repo | Refused at gate 2, exit 1 |
| Run interrupted mid-fast-forward | git's own atomicity applies; the next run re-evaluates from scratch |

## Relationship to the consolidation migration

This setup exists because of
[`../../docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md`](../../docs/superpowers/specs/2026-08-25-skills-repo-consolidation-design.md).
That design retires `bootstrap-sync` on the grounds that once `~/.claude/skills`
owns real files instead of absolute-path symlinks, provisioning a new machine is
just `git clone`. Correct — but *provisioning* and *staying current* are
different problems, and only the first is solved by a clone. This is the second.

It is deliberately not a substitute for either neighbour:

- **`claude-acs backup`** captures content by value, with retention, for
  point-in-time restore. It protects against deletion and corruption.
- **This** moves commits from the remote into this machine, and stops at the
  first sign of local work. It protects against staleness.

Because the target holds uncommitted work, this setup is strictly the weaker,
more cautious of the two, and it should stay that way.

## Testing

```
cd scripts && python3 -m unittest discover -p 'test_*.py'
```

75 tests, stdlib only. Every one builds its own throwaway origin/publisher/clone
in a `TemporaryDirectory`; nothing in the suite reads or resolves the real
`~/.claude/skills`. The safety tests assert negatives — the dirty file is still
byte-identical, HEAD did not move, the local commit is still in `git log`, no
merge commit was fabricated — and the last class statically audits this
package's own source for destructive verbs, shell execution, and any route to
git that bypasses `gitops.run_git`.
