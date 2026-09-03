"""JSONL storage: the tracker's source of truth.

Layout, derived entirely from each interaction's own timestamp (local time) and
detected project name::

    data/usage/YYYY/MM/DD/<project>/prompts.jsonl

One interaction per line. Files are only ever appended to, so a crash mid-write
can lose at most the final line, and the SQLite index (``database.py``) can
always be rebuilt from these files.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .project_detector import sanitize_project_name
from .utils import FileLock, iter_jsonl, to_local

PROMPTS_FILENAME = "prompts.jsonl"


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
def day_dir(usage_dir: Path, moment: _dt.datetime) -> Path:
    """``data/usage/YYYY/MM/DD`` for a moment, bucketed in local time."""
    local = to_local(moment)
    return Path(usage_dir) / local.strftime("%Y") / local.strftime("%m") / local.strftime("%d")


def project_dir(usage_dir: Path, moment: _dt.datetime, project: str) -> Path:
    return day_dir(usage_dir, moment) / sanitize_project_name(project)


def prompts_path(usage_dir: Path, moment: _dt.datetime, project: str) -> Path:
    return project_dir(usage_dir, moment, project) / PROMPTS_FILENAME


def path_for_date_str(usage_dir: Path, date_str: str, project: str) -> Path:
    """Same as :func:`prompts_path` but from a ``YYYY-MM-DD`` string."""
    year, month, day = date_str.split("-")
    return (
        Path(usage_dir) / year / month / day / sanitize_project_name(project) / PROMPTS_FILENAME
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def append_records(usage_dir: Path, records: Sequence[Dict[str, Any]]) -> int:
    """Append interaction records to their day/project files.

    Records are grouped so each file is opened and locked once. The lock guards
    against two Claude Code sessions finishing a turn in the same project at the
    same instant.
    """
    if not records:
        return 0

    grouped: Dict[Path, List[Dict[str, Any]]] = {}
    for record in records:
        target = path_for_date_str(usage_dir, record["date"], record["project"])
        grouped.setdefault(target, []).append(record)

    written = 0
    for target, group in grouped.items():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        lock_path = target.with_name(target.name + ".lock")
        with FileLock(lock_path):
            try:
                with target.open("a", encoding="utf-8", newline="\n") as handle:
                    for record in group:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                written += len(group)
            except OSError:
                continue
    return written


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def _valid_part(name: str, width: int) -> bool:
    return len(name) == width and name.isdigit()


def iter_day_dirs(
    usage_dir: Path,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Iterator[tuple]:
    """Yield ``(date_str, day_path)`` for day directories inside a date range.

    Pruning happens at the directory level, so narrowing to one day never opens
    another day's files.
    """
    base = Path(usage_dir)
    if not base.is_dir():
        return

    for year_dir in sorted(base.iterdir()) if base.is_dir() else []:
        if not year_dir.is_dir() or not _valid_part(year_dir.name, 4):
            continue
        if date_from and year_dir.name < date_from[:4]:
            continue
        if date_to and year_dir.name > date_to[:4]:
            continue

        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not _valid_part(month_dir.name, 2):
                continue
            ym = year_dir.name + "-" + month_dir.name
            if date_from and ym < date_from[:7]:
                continue
            if date_to and ym > date_to[:7]:
                continue

            for date_dir in sorted(month_dir.iterdir()):
                if not date_dir.is_dir() or not _valid_part(date_dir.name, 2):
                    continue
                date_str = ym + "-" + date_dir.name
                if date_from and date_str < date_from:
                    continue
                if date_to and date_str > date_to:
                    continue
                yield date_str, date_dir


def iter_records(
    usage_dir: Path,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    projects: Optional[Iterable[str]] = None,
) -> Iterator[Dict[str, Any]]:
    """Stream stored interactions, optionally restricted by date and project.

    Records are yielded as stored, duplicates included; use
    :func:`dedupe_records` when a unique view is required.
    """
    wanted = {sanitize_project_name(p) for p in projects} if projects else None

    for _date_str, date_dir in iter_day_dirs(usage_dir, date_from, date_to):
        try:
            entries = sorted(date_dir.iterdir())
        except OSError:
            continue
        for proj_dir in entries:
            if not proj_dir.is_dir():
                continue
            if wanted is not None and proj_dir.name not in wanted:
                continue
            target = proj_dir / PROMPTS_FILENAME
            if not target.is_file():
                continue
            for record in iter_jsonl(target):
                yield record


def dedupe_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse records sharing an ``id``, keeping the highest ``revision``.

    A turn is rewritten (with a bumped revision) when late-flushed assistant
    messages change its totals, so the newest revision is the accurate one.
    """
    best: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for record in records:
        key = record.get("id")
        if not isinstance(key, str):
            key = "anon:%d" % len(order)
        if key not in best:
            best[key] = record
            order.append(key)
        elif int(record.get("revision") or 0) >= int(best[key].get("revision") or 0):
            best[key] = record
    return [best[key] for key in order]


def known_projects(usage_dir: Path) -> List[str]:
    """Every project name that has a directory under ``data/usage``."""
    found = set()
    for _date_str, date_dir in iter_day_dirs(usage_dir):
        try:
            for proj_dir in date_dir.iterdir():
                if proj_dir.is_dir() and (proj_dir / PROMPTS_FILENAME).is_file():
                    found.add(proj_dir.name)
        except OSError:
            continue
    return sorted(found)


def count_files(usage_dir: Path) -> int:
    return sum(1 for _ in Path(usage_dir).rglob(PROMPTS_FILENAME))
