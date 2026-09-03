"""Shared filtering and aggregation used by the API server, CLI and reports.

Keeping the filter vocabulary in one place means the dashboard, the CLI and the
generated reports can never drift into answering the same question differently.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

from .database import Database
from .utils import now_local

#: Names accepted by ``--range`` on the CLI and ``?range=`` on the API.
QUICK_RANGES = (
    "today",
    "yesterday",
    "last7",
    "last30",
    "this_month",
    "previous_month",
    "this_year",
    "all",
)


def _iso(day: _dt.date) -> str:
    return day.strftime("%Y-%m-%d")


def resolve_range(name: str, today: Optional[_dt.date] = None) -> Tuple[Optional[str], Optional[str]]:
    """Turn a quick-filter name into ``(date_from, date_to)``.

    ``today`` is always derived from the clock at call time - no date anywhere in
    this system is baked in.
    """
    current = today or now_local().date()
    key = (name or "").strip().lower()

    if key == "today":
        return _iso(current), _iso(current)
    if key == "yesterday":
        previous = current - _dt.timedelta(days=1)
        return _iso(previous), _iso(previous)
    if key in ("last7", "last_7_days", "7d"):
        return _iso(current - _dt.timedelta(days=6)), _iso(current)
    if key in ("last30", "last_30_days", "30d"):
        return _iso(current - _dt.timedelta(days=29)), _iso(current)
    if key in ("this_month", "month"):
        return _iso(current.replace(day=1)), _iso(current)
    if key in ("previous_month", "prev_month", "last_month"):
        first_of_this = current.replace(day=1)
        last_of_previous = first_of_this - _dt.timedelta(days=1)
        return _iso(last_of_previous.replace(day=1)), _iso(last_of_previous)
    if key in ("this_year", "year"):
        return _iso(current.replace(month=1, day=1)), _iso(current)
    return None, None


def month_bounds(year: int, month: int) -> Tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return "%04d-%02d-01" % (year, month), "%04d-%02d-%02d" % (year, month, last)


def build_filters(params: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise raw request/CLI parameters into database filter keys.

    Recognised keys: ``range``, ``from``/``date_from``, ``to``/``date_to``,
    ``year``, ``month``, ``project``, ``model``, ``search``, ``origin``,
    ``session_id``. ``All Projects`` style values are treated as "no filter".
    """
    filters: Dict[str, Any] = {}

    quick = params.get("range")
    if quick and str(quick).lower() != "all":
        date_from, date_to = resolve_range(str(quick))
        if date_from:
            filters["date_from"] = date_from
        if date_to:
            filters["date_to"] = date_to

    for source, target in (
        ("from", "date_from"),
        ("date_from", "date_from"),
        ("to", "date_to"),
        ("date_to", "date_to"),
    ):
        value = params.get(source)
        if value:
            filters[target] = str(value)[:10]

    for key in ("year", "month"):
        value = params.get(key)
        if value in (None, "", "all"):
            continue
        try:
            filters[key] = int(value)
        except (TypeError, ValueError):
            continue

    for key in ("project", "model", "origin", "session_id"):
        value = params.get(key)
        if value and str(value).lower() not in ("all", "__all__"):
            filters[key] = str(value)

    search = params.get("search")
    if search and str(search).strip():
        filters["search"] = str(search).strip()

    return filters


def percentage(part: Optional[int], whole: Optional[int]) -> float:
    if not whole or part is None:
        return 0.0
    return round((part / whole) * 100.0, 2)


def with_percentages(rows: List[Dict[str, Any]], key: str = "total_tokens") -> List[Dict[str, Any]]:
    """Add a ``pct`` field to grouped rows, relative to the group total."""
    whole = sum((row.get(key) or 0) for row in rows)
    for row in rows:
        row["pct"] = percentage(row.get(key), whole)
    return rows


def summarise(db: Database, params: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the dashboard's summary cards need for one filter set."""
    filters = build_filters(params)
    summary = db.summary(filters)
    summary["filters"] = filters
    return summary


def normalise_totals(row: Dict[str, Any]) -> Dict[str, Any]:
    """Replace SQL NULLs with 0 for display, keeping the row shape stable."""
    numeric = (
        "prompts", "human_prompts", "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
        "cache_tokens", "total_tokens",
    )
    for key in numeric:
        if key in row and row[key] is None:
            row[key] = 0
    return row
