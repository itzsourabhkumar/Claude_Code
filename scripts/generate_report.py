#!/usr/bin/env python
"""Generate daily / monthly / yearly usage reports.

    python scripts/generate_report.py
    python scripts/generate_report.py --only daily
    python scripts/generate_report.py --reindex

Reports are written under ``reports/`` and are derived entirely from the data
present, so no date is ever hard-coded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker.config import load_config  # noqa: E402
from tracker.database import open_database  # noqa: E402
from tracker.reports import generate_all  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate Claude Code usage reports")
    parser.add_argument("--only", choices=("daily", "monthly", "yearly"),
                        help="generate just one report family")
    parser.add_argument("--reindex", action="store_true",
                        help="refresh the SQLite index from the JSONL files first")
    args = parser.parse_args(argv)

    config = load_config()
    with open_database(config) as db:
        if args.reindex or db.is_empty():
            stats = db.reindex(config.usage_dir, full=args.reindex)
            print("Indexed %d record(s) from %d file(s)." % (stats["records"], stats["files"]))
        written = generate_all(db, config, only=args.only)

    print("Reports written to " + str(config.reports_dir))
    for period, count in written.items():
        print("  %-8s %d file(s)" % (period, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
