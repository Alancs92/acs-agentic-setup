#!/usr/bin/env python3
"""launchd job generation for claude-skills-sync.

  validate_label       a label becomes a filename, so it is checked, not trusted
  resolve_log_path     where the job's stdout/stderr goes
  render_launchd_plist the plist XML, cadence read from config.json

Mirrors setups/claude-skills-backup/scripts/sync.py's render_launchd_plist and
com.acs.tide-sync.plist: explicit ProgramArguments, EnvironmentVariables with
HOME and a spelled-out PATH, RunAtLoad false, StandardOutPath ==
StandardErrorPath, ProcessType Background. The differences from the backup job
are deliberate and both matter:

  DAILY, not weekly. Pulling is cheap, does nothing when there is nothing to
  pull, and is a no-op whenever the tree is dirty. Its cost is bounded and its
  value decays with staleness, which is the opposite of a backup's profile.

  `pull --quiet`. The structured log line goes to config's log_path, which is
  the same file launchd captures stdout into. Without --quiet every run would
  write the identical line twice. StandardOutPath then carries only the things
  nobody planned for -- a traceback, a git message on stderr -- which is what
  makes tailing it useful.

Inputs   config dict (schedule block), path to skills_sync.py
Outputs  plist XML text
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape

DEFAULT_LABEL = "com.acs.claude-skills-sync"

#: A label becomes "<label>.plist" under ~/Library/LaunchAgents. Anything
#: outside this class -- a slash, a NUL, whitespace -- could place the file
#: somewhere else entirely, so it is rejected rather than sanitised.
LABEL_RE = re.compile(r"[A-Za-z0-9._-]+")

# launchd jobs inherit almost nothing, so PATH is spelled out or the script
# cannot find git. ~/.local/bin is filled in against the real home at render
# time. Matches com.acs.tide-sync.plist and the backup job.
_LAUNCHD_PATH_TAIL = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

_PYTHON = "/usr/bin/python3"


def validate_label(label: str) -> str:
    """Return `label` if it is safe to use as a filename, else raise ValueError."""
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise ValueError(
            "schedule.label must match [A-Za-z0-9._-]+, got: {0!r}".format(label)
        )
    if set(label) <= {"."}:
        # Passes the character class but names the directory itself.
        raise ValueError("schedule.label may not consist only of dots")
    return label


def resolve_log_path(config: dict, label: str) -> Path:
    """Where the job writes.

    Defaults under ~/Library/Logs and refuses to leave it, because launchd does
    not create a missing StandardOutPath directory -- it silently declines to
    spawn the job, with no error anywhere except a bare exit status in
    `launchctl print`. ~/Library/Logs always exists, so anchoring there removes
    the entire failure mode. (~/.claude/logs, the obvious-looking choice, is
    exactly the trap: it does not exist on a fresh machine.)
    """
    home = Path.home()
    logs_root = home / "Library" / "Logs"
    configured = config.get("log_path")
    if not configured:
        return logs_root / "{0}.log".format(label)
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = logs_root / path
    return path


def _plist_string(key: str, value: str) -> str:
    return "\t<key>{0}</key>\n\t<string>{1}</string>".format(
        _xml_escape(key), _xml_escape(value)
    )


def _calendar_dict(pairs: List[Tuple[str, int]]) -> str:
    inner = "".join(
        "<key>{0}</key><integer>{1}</integer>".format(k, v) for k, v in pairs
    )
    return "\t<key>StartCalendarInterval</key>\n\t<dict>{0}</dict>".format(inner)


def schedule_block(interval_days: int, hour: int, minute: int,
                   weekday: int = 0) -> str:
    """StartCalendarInterval for daily/weekly, StartInterval otherwise.

    interval_days == 1 -> daily at hour:minute (the configured default)
    interval_days == 7 -> weekly on `weekday` (0=Sunday .. 6=Saturday)
    otherwise          -> StartInterval, in seconds
    """
    if interval_days == 1:
        return _calendar_dict([("Hour", hour), ("Minute", minute)])
    if interval_days == 7:
        return _calendar_dict(
            [("Weekday", weekday), ("Hour", hour), ("Minute", minute)]
        )
    return "\t<key>StartInterval</key>\n\t<integer>{0}</integer>".format(
        interval_days * 86400
    )


def render_launchd_plist(config: dict, script_path: Path,
                         config_path: Optional[Path] = None) -> str:
    """Return the plist XML for the daily pull job.

    Cadence has exactly one source of truth: config.json's schedule block. Every
    field is range-checked here, because a plist launchd cannot parse is a job
    that silently never runs.
    """
    sched = config.get("schedule", {})
    label = validate_label(str(sched.get("label", DEFAULT_LABEL)))
    interval_days = int(sched.get("interval_days", 1))
    hour = int(sched.get("hour", 7))
    minute = int(sched.get("minute", 15))
    weekday = int(sched.get("weekday", 1))

    if interval_days < 1:
        raise ValueError(
            "schedule.interval_days must be >= 1, got {0}".format(interval_days))
    if not 0 <= hour <= 23:
        raise ValueError("schedule.hour must be 0-23, got {0}".format(hour))
    if not 0 <= minute <= 59:
        raise ValueError("schedule.minute must be 0-59, got {0}".format(minute))
    if not 0 <= weekday <= 6:
        raise ValueError(
            "schedule.weekday must be 0-6 (0=Sunday), got {0}".format(weekday))

    home = Path.home()
    log_path = resolve_log_path(config, label)
    launchd_path = "{0}:{1}".format(home / ".local" / "bin", _LAUNCHD_PATH_TAIL)

    program = [_PYTHON, str(script_path), "pull", "--quiet"]
    if config_path is not None:
        program.extend(["--config", str(config_path)])

    body = "\n\n".join([
        _plist_string("Label", label),
        "\t<key>ProgramArguments</key>\n\t<array>\n"
        + "\n".join("\t\t<string>{0}</string>".format(_xml_escape(a))
                    for a in program)
        + "\n\t</array>",
        "\t<key>EnvironmentVariables</key>\n\t<dict>\n"
        "\t\t<key>HOME</key>\n\t\t<string>{0}</string>\n"
        "\t\t<key>PATH</key>\n\t\t<string>{1}</string>"
        "\n\t</dict>".format(_xml_escape(str(home)), _xml_escape(launchd_path)),
        schedule_block(interval_days, hour, minute, weekday),
        # False: loading or reloading the agent must not trigger an off-schedule
        # run against the user's live working tree.
        "\t<key>RunAtLoad</key>\n\t<false/>",
        _plist_string("StandardOutPath", str(log_path)),
        _plist_string("StandardErrorPath", str(log_path)),
        _plist_string("ProcessType", "Background"),
    ])

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "{0}\n"
        "</dict>\n"
        "</plist>\n".format(body)
    )
