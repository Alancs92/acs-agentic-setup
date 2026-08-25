#!/usr/bin/env python3
"""The append-only run log for claude-skills-sync.

  OUTCOME_*    the closed set of things a pull run can conclude
  append       write one timestamped line
  read_entries parse the log back
  last_success the most recent run that actually reconciled with the remote

WHY THE LOG IS THE PRODUCT
  A scheduled job that mostly does nothing is indistinguishable from a scheduled
  job that is not running at all -- unless it says so. Most pulls here are
  expected to end in `skipped-dirty`, because the user edits skills daily and a
  dirty tree is the normal state, not an error. So "nothing happened" has to be
  recorded as loudly as "something happened", or `status` cannot tell "up to
  date" from "the launchd job was never loaded".

FORMAT
  <utc>  <command>  <outcome>  <key=value ...>

  Two spaces between fields, because a detail value may contain single spaces.
  Line-oriented and greppable on purpose: this file is also launchd's
  StandardOutPath, so it will contain the occasional unstructured traceback, and
  read_entries() must skip those rather than choke on them.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

# Terminal states of a pull run. Kept as one closed set so status(), the README
# table and the tests cannot drift apart.
OUTCOME_FAST_FORWARDED = "fast-forwarded"
OUTCOME_UP_TO_DATE = "up-to-date"
OUTCOME_SKIPPED_DIRTY = "skipped-dirty"
OUTCOME_SKIPPED_AHEAD = "skipped-ahead"
OUTCOME_SKIPPED_DIVERGED = "skipped-diverged"
OUTCOME_SKIPPED_DETACHED = "skipped-detached-head"
OUTCOME_SKIPPED_BRANCH = "skipped-other-branch"
OUTCOME_SKIPPED_NO_UPSTREAM = "skipped-no-upstream"
OUTCOME_REFUSED_NOT_A_REPO = "refused-not-a-repo"
OUTCOME_REFUSED_REMOTE_MISMATCH = "refused-remote-mismatch"
OUTCOME_FETCH_FAILED = "fetch-failed"
OUTCOME_MERGE_FAILED = "merge-failed"

#: Outcomes that prove this machine is reconciled with the remote. Note that
#: `up-to-date` counts: nothing was pulled because there was nothing to pull,
#: which is a successful sync, not a skipped one.
SUCCESS_OUTCOMES = frozenset({OUTCOME_FAST_FORWARDED, OUTCOME_UP_TO_DATE})

#: Outcomes that mean a human has to do something. Everything else is either
#: success or the ordinary "you have unsaved work" case.
ATTENTION_OUTCOMES = frozenset({
    OUTCOME_SKIPPED_DIVERGED,
    OUTCOME_REFUSED_NOT_A_REPO,
    OUTCOME_REFUSED_REMOTE_MISMATCH,
    OUTCOME_MERGE_FAILED,
})

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s\s+"
    r"(?P<command>\S+)\s\s+(?P<outcome>\S+)(?:\s\s+(?P<detail>.*))?$"
)


class Entry(NamedTuple):
    timestamp: str
    command: str
    outcome: str
    detail: str

    @property
    def when(self) -> _dt.datetime:
        return parse_iso(self.timestamp)


def utcnow_iso() -> str:
    """Current UTC time as '2026-08-25T07:15:00Z'.

    Taken once at the top of a run and threaded through, so every line of one
    run shares a timestamp and tests can inject a fixed value.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> _dt.datetime:
    return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


def format_detail(fields: Optional[Dict[str, object]]) -> str:
    """`key=value` pairs in insertion order, blanks and Nones dropped."""
    if not fields:
        return ""
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        parts.append("{0}={1}".format(key, text))
    return " ".join(parts)


def format_line(timestamp: str, command: str, outcome: str,
                fields: Optional[Dict[str, object]] = None) -> str:
    detail = format_detail(fields)
    line = "{0}  {1}  {2}".format(timestamp, command, outcome)
    if detail:
        line += "  " + detail
    return line


def append(log_path, timestamp: str, command: str, outcome: str,
           fields: Optional[Dict[str, object]] = None) -> str:
    """Append one line and return it.

    Opened in append mode per call rather than held open: the job is short-lived
    and a crash mid-run must still leave every line before it on disk. A failure
    to write is swallowed -- losing the log is bad, but aborting a run that has
    already decided not to touch the repository would be worse, and there is
    nowhere better to report it to.
    """
    line = format_line(timestamp, command, outcome, fields)
    path = Path(log_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    return line


def read_entries(log_path) -> List[Entry]:
    """Every parseable line, oldest first. Unparseable lines are skipped.

    Skipping rather than failing is deliberate: this file doubles as launchd's
    StandardOutPath, so a Python traceback can appear between two good lines and
    must not make the log unreadable.
    """
    path = Path(log_path).expanduser()
    entries: List[Entry] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                match = _LINE.match(raw.rstrip("\n"))
                if match is None:
                    continue
                entries.append(Entry(
                    timestamp=match.group("ts"),
                    command=match.group("command"),
                    outcome=match.group("outcome"),
                    detail=match.group("detail") or "",
                ))
    except OSError:
        return []
    return entries


def last_success(log_path) -> Optional[Entry]:
    for entry in reversed(read_entries(log_path)):
        if entry.outcome in SUCCESS_OUTCOMES:
            return entry
    return None


def age_days(entry: Entry, now: Optional[_dt.datetime] = None) -> float:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (now - entry.when).total_seconds() / 86400.0
