#!/usr/bin/env python3
"""claude-acs stats -- cross-account Claude Code usage statistics.

Emits stats.json plus a standalone dashboard.html. Standard library only.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aggregate          # noqa: E402
import cache as cache_mod # noqa: E402
import discovery          # noqa: E402
import render as render_mod  # noqa: E402
import transcripts        # noqa: E402

DEFAULT_OUT = Path.home() / ".cache" / "claude-acs-stats"
TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"


def _local_timezone_name():
    """IANA zone name, best effort. Never fatal."""
    try:
        from zoneinfo import ZoneInfo  # noqa: F401
        link = Path("/etc/localtime")
        if link.is_symlink():
            target = os.readlink(link)
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return os.environ.get("TZ") or None


def parse_args(argv):
    p = argparse.ArgumentParser(prog="claude-acs stats", description=__doc__)
    p.add_argument("--store", default=str(Path.home() / ".claude-accounts"),
                   help="account store dir (default: ~/.claude-accounts)")
    p.add_argument("--canonical", default=str(Path.home() / ".claude"),
                   help="canonical config dir (default: ~/.claude)")
    p.add_argument("--accounts", help="comma-separated account names to include")
    p.add_argument("--since", help="earliest UTC date to include, YYYY-MM-DD")
    p.add_argument("--until", help="latest UTC date to include, YYYY-MM-DD")
    p.add_argument("--out", default=str(DEFAULT_OUT), help=f"output dir (default: {DEFAULT_OUT})")
    # NOTE: Cache.save() writes only keys touched this run, so a filtered run
    # (--accounts / --since) drops cache entries for everything it skipped, and
    # the next full run re-parses them. Documented in the README.
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the parse cache")
    p.add_argument("--json-only", action="store_true", help="skip dashboard.html")
    p.add_argument("--open", action="store_true", help="open the dashboard when done")
    p.add_argument("--quiet", action="store_true", help="suppress progress output")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    store = Path(args.store).expanduser()
    accounts = discovery.discover_accounts(store, Path(args.canonical).expanduser())
    if args.accounts:
        wanted = {n.strip() for n in args.accounts.split(",") if n.strip()}
        accounts = [a for a in accounts if a.name in wanted]

    parse_cache = None
    if not args.no_cache:
        parse_cache = cache_mod.Cache.load(out_dir / "cache.json", transcripts.ROLLUP_SCHEMA)

    def progress(n):
        if not args.quiet and n % 250 == 0:
            print(f"  ... {n} transcripts scanned", file=sys.stderr)

    stats = aggregate.build_stats(
        accounts, store_label=str(store), since=args.since, until=args.until,
        cache=parse_cache, timezone_name=_local_timezone_name(), on_progress=progress)

    if parse_cache:
        parse_cache.save()

    json_path = out_dir / "stats.json"
    json_path.write_text(json.dumps(stats, indent=1))

    html_path = out_dir / "dashboard.html"
    if not args.json_only:
        if not TEMPLATE.is_file():
            print(f"error: template not found at {TEMPLATE}", file=sys.stderr)
            return 1
        html_path.write_text(render_mod.render(TEMPLATE.read_text(), stats))

    if not args.quiet:
        run = stats["run"]
        print(f"stats.json  {json_path}  ({json_path.stat().st_size // 1024} KB)")
        if not args.json_only:
            print(f"dashboard   {html_path}")
        print(f"accounts {len(stats['accounts'])}  union {stats['totals']['union']}  "
              f"sum {stats['totals']['sum_of_totals']}")
        print(f"files {run['files_scanned']} scanned, {run['files_parsed']} parsed, "
              f"{run['cache_hits']} cached, {run['malformed_lines']} malformed lines, "
              f"{run['duration_seconds']}s")
        for skipped in run["accounts_skipped"]:
            print(f"skipped {skipped.get('name')}: {skipped.get('reason')}")

    if args.open and not args.json_only:
        import subprocess
        subprocess.run(["open", str(html_path)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
