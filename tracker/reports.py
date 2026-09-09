"""Daily / monthly / yearly usage reports written as JSON.

Reports are derived views over the index and can be regenerated at any time, so
they are safe to delete. Files land in::

    reports/daily/2026-09-03.json
    reports/monthly/2026-09.json
    reports/yearly/2026.json

Every period covered by the data gets a file; nothing is hard-coded to a
particular year, month or day.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .database import Database
from .pricing import PriceBook
from .query import cost_summary, month_bounds, normalise_totals
from .utils import now_local, write_json_atomic


def _totals(db: Database, filters: Dict[str, Any]) -> Dict[str, Any]:
    return normalise_totals(db.summary(filters))


def _cost(db: Database, filters: Dict[str, Any],
          prices: Optional[PriceBook]) -> Optional[Dict[str, Any]]:
    """Cost for a report period, or None when pricing is switched off."""
    if prices is None or not prices.enabled:
        return None
    return cost_summary(db, filters, prices)


def _projects(db: Database, filters: Dict[str, Any]) -> Dict[str, int]:
    """Project -> total tokens, largest first."""
    rows = [normalise_totals(row) for row in db.by_project(filters)]
    rows.sort(key=lambda row: row.get("total_tokens") or 0, reverse=True)
    return {row["project"]: row.get("total_tokens") or 0 for row in rows if row.get("project")}


def _shape(period_key: str, period_value: str, totals: Dict[str, Any],
           projects: Dict[str, int], extra: Optional[Dict[str, Any]] = None,
           cost: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    report = {
        period_key: period_value,
        "total_prompts": totals.get("prompts") or 0,
        "human_prompts": totals.get("human_prompts") or 0,
        "input_tokens": totals.get("input_tokens") or 0,
        "output_tokens": totals.get("output_tokens") or 0,
        "cache_read_input_tokens": totals.get("cache_read_input_tokens") or 0,
        "cache_creation_input_tokens": totals.get("cache_creation_input_tokens") or 0,
        "cache_tokens": totals.get("cache_tokens") or 0,
        "total_tokens": totals.get("total_tokens") or 0,
        "projects_used": totals.get("projects") or 0,
        "projects": projects,
        "generated_at": now_local().isoformat(timespec="seconds"),
    }
    if cost is not None:
        # Derived from the same token counts printed above, at read time.
        report["cost"] = {
            "currency": "INR",
            "input_cost_inr": cost.get("input_cost_inr"),
            "cached_input_cost_inr": cost.get("cached_input_cost_inr"),
            "output_cost_inr": cost.get("output_cost_inr"),
            "total_cost_inr": cost.get("total_cost_inr"),
            "total_cost_usd": cost.get("total_cost_usd"),
            "usd_to_inr": cost.get("usd_to_inr"),
            "cache_write_ttl": cost.get("cache_write_ttl"),
            "pricing_as_of": cost.get("pricing_as_of"),
            "unpriced_tokens": cost.get("unpriced_tokens"),
        }
    report.update(extra or {})
    return report


def daily_report(db: Database, date: str,
                 prices: Optional[PriceBook] = None) -> Dict[str, Any]:
    filters = {"date_from": date, "date_to": date}
    return _shape("date", date, _totals(db, filters), _projects(db, filters),
                  cost=_cost(db, filters, prices))


def monthly_report(db: Database, year: int, month: int,
                   prices: Optional[PriceBook] = None) -> Dict[str, Any]:
    start, end = month_bounds(year, month)
    filters = {"date_from": start, "date_to": end}
    days = {
        row["date"]: (row.get("total_tokens") or 0)
        for row in (normalise_totals(r) for r in db.by_date(filters))
    }
    return _shape(
        "month", "%04d-%02d" % (year, month),
        _totals(db, filters), _projects(db, filters),
        {"days": days, "active_days": len(days)},
        cost=_cost(db, filters, prices),
    )


def yearly_report(db: Database, year: int,
                  prices: Optional[PriceBook] = None) -> Dict[str, Any]:
    filters = {"date_from": "%04d-01-01" % year, "date_to": "%04d-12-31" % year}
    months: Dict[str, int] = {}
    for row in (normalise_totals(r) for r in db.by_date(filters)):
        months[row["date"][:7]] = months.get(row["date"][:7], 0) + (row.get("total_tokens") or 0)
    return _shape(
        "year", str(year),
        _totals(db, filters), _projects(db, filters),
        {"months": dict(sorted(months.items())), "active_months": len(months)},
        cost=_cost(db, filters, prices),
    )


def _periods(db: Database) -> Dict[str, List]:
    """Every day, month and year present in the index."""
    dates = [row["date"] for row in db.by_date({}) if row.get("date")]
    months = sorted({d[:7] for d in dates})
    years = sorted({d[:4] for d in dates})
    return {"dates": sorted(dates), "months": months, "years": years}


def generate_all(db: Database, config: Config, only: Optional[str] = None) -> Dict[str, int]:
    """Write every report the data supports.

    ``only`` restricts generation to ``daily``, ``monthly`` or ``yearly``.
    """
    base = Path(config.reports_dir)
    prices = PriceBook.from_config(config)
    periods = _periods(db)
    written = {"daily": 0, "monthly": 0, "yearly": 0}

    if only in (None, "daily"):
        for date in periods["dates"]:
            if write_json_atomic(base / "daily" / (date + ".json"),
                                 daily_report(db, date, prices)):
                written["daily"] += 1

    if only in (None, "monthly"):
        for stamp in periods["months"]:
            year, month = int(stamp[:4]), int(stamp[5:7])
            if write_json_atomic(
                base / "monthly" / (stamp + ".json"), monthly_report(db, year, month, prices)
            ):
                written["monthly"] += 1

    if only in (None, "yearly"):
        for stamp in periods["years"]:
            if write_json_atomic(
                base / "yearly" / (stamp + ".json"), yearly_report(db, int(stamp), prices)
            ):
                written["yearly"] += 1

    return written
