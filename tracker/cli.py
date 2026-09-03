"""Command-line interface.

The same commands work on Windows, Linux and macOS; only the name of the
interpreter differs (``python`` on Windows, ``python3`` on Linux and macOS).

    python -m tracker.cli today
    python -m tracker.cli yesterday
    python -m tracker.cli month
    python -m tracker.cli year
    python -m tracker.cli project data-platform
    python -m tracker.cli range --from 2026-09-01 --to 2026-09-03
    python -m tracker.cli search ClickHouse
    python -m tracker.cli report
    python -m tracker.cli backfill        # import existing Claude Code history
    python -m tracker.cli reindex         # rebuild the SQLite index from JSONL
    python -m tracker.cli status          # installation / data health check
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __name__ == "__main__" and __package__ in (None, ""):  # allow `python cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "tracker"

from . import TRACKER_VERSION
from .collector import Collector
from .config import InvalidConfig, load_config
from .platform_utils import (
    dashboard_launcher,
    describe_platform,
    python_command_name,
)
from .database import open_database
from .parser import iter_transcripts
from .query import build_filters, normalise_totals, resolve_range
from .reports import generate_all
from .utils import now_local


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _fmt(value: Optional[int]) -> str:
    return "-" if value is None else format(int(value), ",")


def print_usage_block(title: str, totals: Dict[str, Any], projects: List[Dict[str, Any]]) -> None:
    totals = normalise_totals(dict(totals))
    print()
    print("Claude Code Usage - " + title)
    print()
    print("Prompts       : " + _fmt(totals.get("prompts")))
    print("  (human)     : " + _fmt(totals.get("human_prompts")))
    print("Input Tokens  : " + _fmt(totals.get("input_tokens")))
    print("Output Tokens : " + _fmt(totals.get("output_tokens")))
    print("Cache Tokens  : " + _fmt(totals.get("cache_tokens")))
    print("Total Tokens  : " + _fmt(totals.get("total_tokens")))

    if projects:
        width = max(len(str(row.get("project") or "?")) for row in projects)
        width = max(width, 12)
        print()
        print("Projects:")
        print()
        for row in projects:
            row = normalise_totals(dict(row))
            print("  %-*s  %14s" % (width, row.get("project") or "?", _fmt(row.get("total_tokens"))))
    else:
        print()
        print("No usage recorded for this period.")
    print()


def _range_for(command: str) -> tuple:
    if command in ("today", "yesterday"):
        return resolve_range(command)
    if command == "month":
        return resolve_range("this_month")
    if command == "year":
        return resolve_range("this_year")
    return None, None


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_period(args, config) -> int:
    date_from, date_to = _range_for(args.command)
    filters = build_filters({"from": date_from, "to": date_to})
    with open_database(config) as db:
        totals = db.summary(filters)
        projects = db.by_project(filters)
    label = {
        "today": now_local().strftime("%d %b %Y"),
        "yesterday": (now_local() - _dt.timedelta(days=1)).strftime("%d %b %Y"),
        "month": now_local().strftime("%B %Y"),
        "year": now_local().strftime("%Y"),
    }[args.command]
    print_usage_block(label, totals, projects)
    return 0


def cmd_range(args, config) -> int:
    filters = build_filters({"from": args.date_from, "to": args.date_to, "range": args.range})
    with open_database(config) as db:
        totals = db.summary(filters)
        projects = db.by_project(filters)
    label = "%s to %s" % (
        filters.get("date_from") or "beginning",
        filters.get("date_to") or "today",
    )
    print_usage_block(label, totals, projects)
    return 0


def cmd_project(args, config) -> int:
    filters = build_filters(
        {"project": args.name, "from": args.date_from, "to": args.date_to, "range": args.range}
    )
    with open_database(config) as db:
        totals = db.summary(filters)
        by_date = db.by_date(filters)
        models = db.by_model(filters)

    print_usage_block("project " + args.name, totals, [])
    if by_date:
        print("By date:")
        print()
        for row in by_date:
            row = normalise_totals(dict(row))
            print("  %-12s  %14s  (%s prompts)" % (
                row["date"], _fmt(row.get("total_tokens")), _fmt(row.get("prompts"))))
        print()
    if models:
        print("By model:")
        print()
        for row in models:
            row = normalise_totals(dict(row))
            print("  %-22s  %14s" % (row.get("model") or "unknown", _fmt(row.get("total_tokens"))))
        print()
    return 0


def cmd_projects(args, config) -> int:
    filters = build_filters({"from": args.date_from, "to": args.date_to, "range": args.range})
    with open_database(config) as db:
        rows = [normalise_totals(dict(r)) for r in db.by_project(filters)]
    if not rows:
        print("No usage recorded yet.")
        return 0
    whole = sum(row.get("total_tokens") or 0 for row in rows) or 1
    width = max(max(len(str(r["project"])) for r in rows), 24)
    print()
    print("%-*s %9s %14s %14s %14s %14s %7s" % (
        width, "Project", "Prompts", "Input", "Output", "Cache", "Total", "%"))
    print("-" * (width + 78))
    for row in rows:
        print("%-*s %9s %14s %14s %14s %14s %6.1f%%" % (
            width, row["project"], _fmt(row.get("prompts")), _fmt(row.get("input_tokens")),
            _fmt(row.get("output_tokens")), _fmt(row.get("cache_tokens")),
            _fmt(row.get("total_tokens")), (row.get("total_tokens") or 0) * 100.0 / whole))
    print()
    return 0


def cmd_search(args, config) -> int:
    filters = build_filters(
        {"search": args.text, "from": args.date_from, "to": args.date_to,
         "range": args.range, "project": args.project}
    )
    with open_database(config) as db:
        result = db.prompts(filters, limit=args.limit)
    print()
    print("%d interaction(s) matching %r" % (result["total"], args.text))
    print()
    for row in result["rows"]:
        prompt = (row.get("prompt") or "").replace("\n", " ")
        if len(prompt) > 90:
            prompt = prompt[:87] + "..."
        print("  %s  %-22s  %10s tokens" % (
            (row.get("timestamp") or "")[:16], (row.get("project") or "?")[:22],
            _fmt(row.get("total_tokens"))))
        print("      " + prompt)
    print()
    return 0


def cmd_report(args, config) -> int:
    with open_database(config) as db:
        written = generate_all(db, config, only=args.only)
    print("Reports written to " + str(config.reports_dir))
    for period, count in written.items():
        print("  %-8s %d file(s)" % (period, count))
    return 0


def cmd_backfill(args, config) -> int:
    paths = list(iter_transcripts(args.projects_dir))
    print("Scanning %d Claude Code transcript(s)..." % len(paths))
    with Collector(config) as collector:
        stats = collector.backfill(paths)
    print("Imported %d new interaction(s) from %d transcript(s)."
          % (stats["records"], stats["transcripts"]))
    if stats.get("skipped"):
        print("Skipped %d transcript(s) - re-run to continue." % stats["skipped"])
    return 0


def cmd_reindex(args, config) -> int:
    with open_database(config) as db:
        stats = db.reindex(config.usage_dir, full=not args.incremental)
    print("Indexed %d record(s) from %d file(s) into %s"
          % (stats["records"], stats["files"], config.index_path))
    return 0


def cmd_status(args, config) -> int:
    """Installation and data health, including everything platform-dependent."""
    from .hooks import describe_installation

    with open_database(config) as db:
        summary = normalise_totals(db.summary({}))
        projects = db.distinct("project")

    env = describe_platform(config.root)

    print()
    print("Claude Code Token Usage Tracker v" + TRACKER_VERSION)
    print()
    print("  os              : %s (%s)" % (env["os"], env["platform"]))
    print("  python          : %s" % env["python"])
    print("  git             : " + str(env["git_executable"]))
    print("  claude cli      : " + str(env["claude_executable"]))
    print()
    print("  root            : " + str(config.root))
    print("  usage data      : " + str(config.usage_dir))
    print("  index           : " + str(config.index_path))
    print("  logs            : " + str(config.logs_dir))
    print("  timezone        : " + config.timezone)
    print("  store prompts   : " + ("yes" if config.store_prompt_text else "no (hash only)"))
    print()
    print("  interactions    : " + _fmt(summary.get("prompts")))
    print("  projects        : " + _fmt(len(projects)))
    print("  total tokens    : " + _fmt(summary.get("total_tokens")))
    print("  date range      : %s .. %s" % (
        summary.get("first_date") or "-", summary.get("last_date") or "-"))
    print()
    for line in describe_installation():
        print("  " + line)
    print()
    try:
        host = config.host
    except InvalidConfig as exc:
        print("  dashboard       : misconfigured - %s" % exc)
    else:
        print("  dashboard       : http://%s:%d  (%s, or %s server.py)"
              % (host, config.port, dashboard_launcher(), python_command_name()))
    print()
    return 0


def cmd_json(args, config) -> int:
    """Machine-readable summary, handy for scripting and for the tests."""
    filters = build_filters(
        {"from": args.date_from, "to": args.date_to, "range": args.range,
         "project": args.project, "model": args.model, "search": args.search}
    )
    with open_database(config) as db:
        payload = {
            "summary": normalise_totals(db.summary(filters)),
            "projects": [normalise_totals(dict(r)) for r in db.by_project(filters)],
            "daily": [normalise_totals(dict(r)) for r in db.by_date(filters)],
        }
    print(json.dumps(payload, indent=2, default=str))
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracker.cli",
        description="Claude Code token usage tracker",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def add_range_args(sub):
        sub.add_argument("--from", dest="date_from", help="start date YYYY-MM-DD")
        sub.add_argument("--to", dest="date_to", help="end date YYYY-MM-DD")
        sub.add_argument("--range", dest="range", help="today|yesterday|last7|last30|this_month|previous_month|this_year|all")
        return sub

    for name, help_text in (
        ("today", "usage for today"),
        ("yesterday", "usage for yesterday"),
        ("month", "usage for the current month"),
        ("year", "usage for the current year"),
    ):
        subs.add_parser(name, help=help_text).set_defaults(func=cmd_period)

    add_range_args(subs.add_parser("range", help="usage for an explicit date range")).set_defaults(func=cmd_range)

    project = add_range_args(subs.add_parser("project", help="usage for one project"))
    project.add_argument("name")
    project.set_defaults(func=cmd_project)

    add_range_args(subs.add_parser("projects", help="per-project table")).set_defaults(func=cmd_projects)

    search = add_range_args(subs.add_parser("search", help="search prompt text"))
    search.add_argument("text")
    search.add_argument("--project")
    search.add_argument("--limit", type=int, default=25)
    search.set_defaults(func=cmd_search)

    report = subs.add_parser("report", help="write daily/monthly/yearly JSON reports")
    report.add_argument("--only", choices=("daily", "monthly", "yearly"))
    report.set_defaults(func=cmd_report)

    backfill = subs.add_parser("backfill", help="import existing Claude Code transcripts")
    backfill.add_argument("--projects-dir", type=Path, default=None,
                          help="override ~/.claude/projects")
    backfill.set_defaults(func=cmd_backfill)

    reindex = subs.add_parser("reindex", help="rebuild the SQLite index from the JSONL files")
    reindex.add_argument("--incremental", action="store_true",
                         help="only re-read files that changed")
    reindex.set_defaults(func=cmd_reindex)

    subs.add_parser("status", help="show installation and data health").set_defaults(func=cmd_status)

    as_json = add_range_args(subs.add_parser("json", help="machine-readable summary"))
    as_json.add_argument("--project")
    as_json.add_argument("--model")
    as_json.add_argument("--search")
    as_json.set_defaults(func=cmd_json)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    config.ensure_directories()
    for attr in ("date_from", "date_to", "range"):
        if not hasattr(args, attr):
            setattr(args, attr, None)
    try:
        return args.func(args, config)
    except InvalidConfig as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print("Permission denied: %s\n"
              "Check that you can write to %s" % (exc, config.data_dir), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
