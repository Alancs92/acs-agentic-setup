#!/usr/bin/env python3
"""claude-acs skills -- keep this machine's ~/.claude/skills current with its
remote, without ever being able to lose local work.

    claude-acs skills pull              fast-forward if, and only if, it is safe
    claude-acs skills status            one glanceable paragraph; exit code == alarm
    claude-acs skills install-schedule  generate + load the daily launchd job

THE ONE IDEA
  `~/.claude/skills` is a git working tree (the `acs-claude-skills` repo) AND
  the live directory Claude Code reads skills from AND the place the user is
  editing a half-finished skill right now. Those three roles share one
  directory, which means an unattended sync job here is holding the only copy of
  in-progress work.

  So this is a *pull-only* tool with no reconciliation logic at all. Every state
  it does not fully understand ends the same way: log it, change nothing, let a
  human look. It can fast-forward a clean tree. That is the entire set of things
  it can do. See gitops.py for how that is enforced rather than merely intended.

  What it deliberately cannot do: stash, reset, clean, checkout, restore,
  rebase, merge non-fast-forward, force anything. There is no flag for it.

PULL GATES, in order, each one a hard stop
  1. target is a git work tree AND its own toplevel        else refuse   (exit 1)
  2. origin matches the configured remote                  else refuse   (exit 1)
  3. fetch                                                 else report   (exit 1)
  4. working tree dirty (modified OR untracked)            then skip     (exit 0)
  5. local ahead AND behind (diverged)                     then skip     (exit 2)
  6. clean, behind only  -> merge --ff-only                              (exit 0)

  Gate 4 exits 0 on purpose. A dirty tree is the *expected* daily state for
  someone who writes skills, not a fault, and a job that alarms on the normal
  case gets muted, and a muted job is not a job.

Inputs   ../config.json (or --config), the target repo, the run log
Outputs  a line in the run log; a summary on stdout unless --quiet
Exit     0 fine, 1 refused/failed, 2 needs a human
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gitops                 # noqa: E402
import runlog                 # noqa: E402
import schedule as schedule_mod  # noqa: E402

SETUP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SETUP_DIR / "config.json"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ATTENTION = 2

DEFAULT_STALE_AFTER_DAYS = 14


# --- config -----------------------------------------------------------------

def load_config(path) -> dict:
    """Read config.json, failing loudly on anything missing.

    No defaults for `target`: a sync tool that silently invents which repository
    to operate on is precisely the failure this whole setup exists to prevent.
    """
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "target" not in config:
        raise SystemExit("config.json missing required section: target")
    target = config["target"]
    for key in ("path", "remote", "branch"):
        if not target.get(key):
            raise SystemExit(
                "config.json target.{0} is required and must be non-empty".format(key)
            )
    return config


def target_path(config: dict) -> Path:
    return Path(config["target"]["path"]).expanduser()


def remote_name(config: dict) -> str:
    return config["target"].get("remote_name") or "origin"


def log_path(config: dict) -> Path:
    label = config.get("schedule", {}).get("label", schedule_mod.DEFAULT_LABEL)
    return schedule_mod.resolve_log_path(config, label)


def stale_after_days(config: dict) -> float:
    return float(config.get("policy", {}).get(
        "stale_after_days", DEFAULT_STALE_AFTER_DAYS))


# --- shared reporting -------------------------------------------------------

class Reporter:
    """Writes one line to the run log and, unless quiet, to stdout.

    Every terminal path in pull() goes through this, so "log every outcome with
    a timestamp" is structural rather than something each branch remembers.
    """

    def __init__(self, path, command: str, quiet: bool = False,
                 now: Optional[str] = None) -> None:
        self.path = path
        self.command = command
        self.quiet = quiet
        self.now = now or runlog.utcnow_iso()

    def emit(self, outcome: str, exit_code: int,
             fields: Optional[Dict[str, object]] = None,
             message: str = "") -> int:
        line = runlog.append(self.path, self.now, self.command, outcome, fields)
        if not self.quiet:
            print(line)
            if message:
                print(message)
        elif message and exit_code != EXIT_OK:
            # Under launchd stdout is the log file; a suppressed run still has
            # to surface the loud cases, and stderr is where those belong.
            sys.stderr.write(message + "\n")
        return exit_code


# --- pull -------------------------------------------------------------------

def _identity_check(config: dict, state: gitops.RepoState,
                    path: Path) -> Optional[Tuple[str, Dict[str, object], str]]:
    """Gates 1 and 2. Returns None when the target is provably the right repo.

    The toplevel comparison is not paranoia for its own sake. `~/.claude` being
    (or becoming) a git repo while `~/.claude/skills` is an ordinary directory
    inside it would make `rev-parse` answer happily from the parent, and every
    later check -- remote, branch, dirtiness, ahead/behind -- would then be
    describing a repository we were never asked to touch.
    """
    if not state.is_repo:
        return (
            runlog.OUTCOME_REFUSED_NOT_A_REPO,
            {"path": str(path)},
            "REFUSING: {0} is not a git work tree. Nothing was touched.".format(path),
        )

    if state.toplevel is not None:
        resolved_target = path.resolve()
        resolved_top = Path(state.toplevel).resolve()
        if resolved_top != resolved_target:
            return (
                runlog.OUTCOME_REFUSED_NOT_A_REPO,
                {"path": str(path), "toplevel": str(resolved_top)},
                "REFUSING: {0} is not itself a repository root -- it sits inside "
                "{1}. Refusing to operate on a repository that was not the "
                "configured target.".format(path, resolved_top),
            )

    expected = gitops.normalize_remote(config["target"]["remote"])
    actual = gitops.normalize_remote(state.remote_url)
    if actual != expected:
        return (
            runlog.OUTCOME_REFUSED_REMOTE_MISMATCH,
            {"expected": expected or "<unset>", "actual": actual or "<unset>"},
            "REFUSING: {0} has {1} = {2!r}, expected {3!r}. This is not the "
            "repository this setup is configured for; nothing was touched."
            .format(path, remote_name(config), state.remote_url,
                    config["target"]["remote"]),
        )
    return None


def cmd_pull(config: dict, args) -> int:
    path = target_path(config)
    remote = remote_name(config)
    branch = config["target"]["branch"]
    reporter = Reporter(log_path(config), "pull", quiet=getattr(args, "quiet", False))

    state = gitops.read_state(path, remote, branch)

    # --- gates 1 and 2: is this even the right repository? ------------------
    refusal = _identity_check(config, state, path)
    if refusal is not None:
        outcome, fields, message = refusal
        return reporter.emit(outcome, EXIT_REFUSED, fields, message)

    # --- gate 3: fetch. Writes remote-tracking refs only. -------------------
    fetched = gitops.fetch(path, remote)
    if fetched.returncode != 0:
        detail = (fetched.stderr.strip() or fetched.stdout.strip()
                  or "no output").splitlines()[-1]
        return reporter.emit(
            runlog.OUTCOME_FETCH_FAILED, EXIT_REFUSED,
            {"remote": remote, "error": detail},
            "fetch failed ({0}). The repository was not modified.".format(detail),
        )

    # Re-read: the fetch moved refs, so ahead/behind from before it are stale.
    state = gitops.read_state(path, remote, branch)

    # --- gate 4: dirty tree. The common case, and never an error. -----------
    if state.dirty:
        return reporter.emit(
            runlog.OUTCOME_SKIPPED_DIRTY, EXIT_OK,
            {"branch": state.branch, "modified": state.modified,
             "untracked": state.untracked, "behind": state.behind},
            "",
        )

    if state.branch is None:
        return reporter.emit(
            runlog.OUTCOME_SKIPPED_DETACHED, EXIT_OK,
            {"head": (state.head or "")[:12]},
            "HEAD is detached; leaving it alone.",
        )

    if state.branch != branch:
        return reporter.emit(
            runlog.OUTCOME_SKIPPED_BRANCH, EXIT_OK,
            {"on": state.branch, "configured": branch},
            "on branch {0!r}, configured branch is {1!r}; leaving it alone."
            .format(state.branch, branch),
        )

    if not state.upstream_known:
        return reporter.emit(
            runlog.OUTCOME_SKIPPED_NO_UPSTREAM, EXIT_OK,
            {"ref": gitops.tracking_ref(remote, branch)},
            "no remote-tracking ref {0} after fetch; nothing to compare against."
            .format(gitops.tracking_ref(remote, branch)),
        )

    # --- gate 5: diverged. Loud, and never resolved automatically. ----------
    if state.diverged:
        return reporter.emit(
            runlog.OUTCOME_SKIPPED_DIVERGED, EXIT_ATTENTION,
            {"branch": state.branch, "ahead": state.ahead, "behind": state.behind},
            "\n".join([
                "*** DIVERGED — MANUAL ACTION REQUIRED ***",
                "  {0} has {1} local commit(s) the remote does not have,".format(
                    path, state.ahead),
                "  and the remote has {0} commit(s) this machine does not.".format(
                    state.behind),
                "  Nothing was merged, rebased, reset or stashed. This tool does",
                "  not reconcile history; resolve it by hand, then pulls resume.",
            ]),
        )

    if state.behind == 0:
        outcome = (runlog.OUTCOME_SKIPPED_AHEAD if state.ahead > 0
                   else runlog.OUTCOME_UP_TO_DATE)
        message = ("{0} local commit(s) not yet pushed; nothing to pull."
                   .format(state.ahead) if state.ahead > 0 else "")
        return reporter.emit(
            outcome, EXIT_OK,
            {"branch": state.branch, "ahead": state.ahead,
             "head": (state.head or "")[:12]},
            message,
        )

    # --- gate 6: clean, strictly behind -> the one mutating operation ------
    before = state.head
    ref = gitops.tracking_ref(remote, branch)
    merged = gitops.merge_ff_only(path, ref)
    if merged.returncode != 0:
        detail = (merged.stderr.strip() or merged.stdout.strip()
                  or "no output").splitlines()[-1]
        return reporter.emit(
            runlog.OUTCOME_MERGE_FAILED, EXIT_ATTENTION,
            {"ref": ref, "error": detail},
            "fast-forward refused by git ({0}). Nothing was changed; this tool "
            "does not fall back to a merge.".format(detail),
        )

    after = gitops.head_commit(path)
    return reporter.emit(
        runlog.OUTCOME_FAST_FORWARDED, EXIT_OK,
        {"branch": branch, "commits": state.behind,
         "from": (before or "")[:12], "to": (after or "")[:12]},
        "",
    )


# --- status -----------------------------------------------------------------

def cmd_status(config: dict, args) -> int:
    """A glanceable report, and an exit code a briefing script can branch on.

    Does not fetch by default: this is meant to be called from a morning
    briefing, where a network round-trip that can hang is a worse trade than
    ahead/behind numbers that are as fresh as the last scheduled pull. `--fetch`
    when accuracy matters more than latency.
    """
    path = target_path(config)
    remote = remote_name(config)
    branch = config["target"]["branch"]
    lpath = log_path(config)

    state = gitops.read_state(path, remote, branch)
    refusal = _identity_check(config, state, path)
    if refusal is not None:
        _outcome, _fields, message = refusal
        print(message)
        return EXIT_REFUSED

    if getattr(args, "fetch", False):
        fetched = gitops.fetch(path, remote)
        if fetched.returncode != 0:
            print("fetch      FAILED ({0})".format(
                (fetched.stderr.strip() or "no output").splitlines()[-1]))
        state = gitops.read_state(path, remote, branch)

    attention = []
    if state.diverged:
        attention.append("diverged")

    success = runlog.last_success(lpath)
    entries = runlog.read_entries(lpath)
    if success is None:
        last_text = "never" if not entries else "never (runs recorded, none succeeded)"
        if entries:
            attention.append("no successful sync on record")
    else:
        age = runlog.age_days(success)
        last_text = "{0}  ({1:.1f} days ago)".format(success.timestamp, age)
        if age > stale_after_days(config):
            attention.append("last sync {0:.0f} days old".format(age))

    print("repo       {0}".format(path))
    print("branch     {0}".format(state.branch or "DETACHED HEAD"))
    print("dirty      {0} modified, {1} untracked".format(
        state.modified, state.untracked))
    if state.upstream_known:
        print("unpushed   {0} commit(s)".format(state.ahead))
        print("behind     {0} commit(s)".format(state.behind))
    else:
        print("unpushed   unknown (no {0})".format(
            gitops.tracking_ref(remote, branch)))
        print("behind     unknown")
    print("last sync  {0}".format(last_text))

    if attention:
        print("ATTENTION  " + "; ".join(attention))
        return EXIT_ATTENTION
    print("status     ok")
    return EXIT_OK


# --- install-schedule -------------------------------------------------------

def cmd_install_schedule(config: dict, args) -> int:
    sched = config.get("schedule", {})
    label = str(sched.get("label", schedule_mod.DEFAULT_LABEL))
    # The label becomes a filename under ~/Library/LaunchAgents. Validate before
    # it is ever joined to a path, so a hand-edited config cannot place a plist
    # somewhere else.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
        raise SystemExit(
            "schedule.label must match [A-Za-z0-9._-]+, got: {0!r}".format(label))
    schedule_mod.validate_label(label)

    plist_xml = schedule_mod.render_launchd_plist(
        config, Path(__file__).resolve(), Path(args.config).resolve()
    )
    target = Path("~/Library/LaunchAgents").expanduser() / "{0}.plist".format(label)

    if args.print_only:
        print(plist_xml)
        return EXIT_OK

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(target.parent), delete=False
    ) as tmp:
        tmp.write(plist_xml)
        tmp_path = Path(tmp.name)
    os.replace(str(tmp_path), str(target))

    # argv lists, never a shell string and never os.system: `target` is built
    # from a config-supplied label and must not be able to reach a shell.
    subprocess.run(["launchctl", "unload", str(target)],
                   capture_output=True, check=False)
    completed = subprocess.run(["launchctl", "load", str(target)],
                               capture_output=True, check=False)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))

    interval = int(sched.get("interval_days", 1))
    cadence = "daily" if interval == 1 else "every {0} day(s)".format(interval)
    print("installed  {0}".format(target))
    print("cadence    {0} at {1:02d}:{2:02d}".format(
        cadence, int(sched.get("hour", 7)), int(sched.get("minute", 15))))
    print("log        {0}".format(schedule_mod.resolve_log_path(config, label)))
    return EXIT_OK if completed.returncode == 0 else EXIT_REFUSED


# --- CLI --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-acs skills",
        description="Pull-only sync for ~/.claude/skills. Never stashes, "
                    "resets, checks out or force-anythings; a dirty tree is "
                    "skipped, not resolved.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="path to config.json")
    sub = parser.add_subparsers(dest="command")

    p_pull = sub.add_parser(
        "pull", help="fast-forward if the tree is clean and strictly behind")
    p_pull.add_argument("--quiet", action="store_true",
                        help="write the run log only; no stdout on success")
    p_pull.set_defaults(func=cmd_pull)

    p_status = sub.add_parser(
        "status", help="branch, dirt, drift, last sync; exit 2 if attention needed")
    p_status.add_argument("--fetch", action="store_true",
                          help="refresh remote-tracking refs first (network)")
    p_status.set_defaults(func=cmd_status)

    p_sched = sub.add_parser("install-schedule",
                             help="generate + load the daily launchd job")
    p_sched.add_argument("--print-only", action="store_true",
                         help="print the plist, install nothing")
    p_sched.set_defaults(func=cmd_install_schedule)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    config = load_config(args.config)
    return args.func(config, args)


if __name__ == "__main__":
    sys.exit(main())
