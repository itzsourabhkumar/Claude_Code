"""Index, aggregation, filtering and search (Steps 11 and 17)."""

from __future__ import annotations

import datetime as _dt

import pytest

from tracker.collector import Collector
from tracker.database import load_records, open_database
from tracker.query import build_filters, resolve_range, with_percentages
from tracker.storage import iter_records


@pytest.fixture
def populated(config, make_transcript):
    """Three projects across three days, with known token totals."""
    base = _dt.datetime.now(_dt.timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    plan = [
        ("data-platform", "fix/clickhouse-startup", "Fix the ClickHouse startup error", 0, 1000, 200),
        ("data-platform", "fix/clickhouse-startup", "Another ClickHouse question", 0, 2000, 400),
        ("data-platform", "main", "Unrelated work on the API", 1, 3000, 600),
        ("AccentHRP", "main", "Add payroll export", 1, 4000, 800),
        ("ReviewMint", "main", "Fix review sync", 2, 5000, 1000),
    ]
    for project, branch, prompt, day_offset, cache, out in plan:
        moment = base - _dt.timedelta(days=day_offset)
        builder = make_transcript(
            "slug-" + project + str(day_offset) + str(out),
            "D:\\Projects\\" + project, branch=branch,
        )
        builder.prompt(prompt, moment)
        builder.reply(moment + _dt.timedelta(seconds=1), model="claude-opus-5",
                      input_tokens=10, output_tokens=out,
                      cache_read=cache, cache_creation=100)
        with Collector(config) as collector:
            collector.sync_transcript(builder.write(), finalize_all=True)
    return config


class TestAggregation:
    def test_summary_totals(self, populated):
        with open_database(populated) as db:
            summary = db.summary({})
        assert summary["prompts"] == 5
        assert summary["projects"] == 3
        assert summary["output_tokens"] == 200 + 400 + 600 + 800 + 1000
        assert summary["cache_read_input_tokens"] == 1000 + 2000 + 3000 + 4000 + 5000
        assert summary["cache_creation_input_tokens"] == 500
        assert summary["cache_tokens"] == 15000 + 500
        assert summary["input_tokens"] == 50

    def test_totals_match_the_jsonl_source_of_truth(self, populated):
        """The index is only an index - JSONL must agree with it exactly."""
        records = load_records(populated)
        from_files = sum(r["usage"]["total_tokens"] or 0 for r in records)
        with open_database(populated) as db:
            assert db.summary({})["total_tokens"] == from_files

    def test_grouping_by_project(self, populated):
        with open_database(populated) as db:
            rows = {r["project"]: r for r in db.by_project({})}
        assert rows["data-platform"]["prompts"] == 3
        assert rows["AccentHRP"]["prompts"] == 1
        assert rows["data-platform"]["output_tokens"] == 1200

    def test_percentages_sum_to_one_hundred(self, populated):
        with open_database(populated) as db:
            rows = with_percentages(db.by_project({}))
        assert round(sum(r["pct"] for r in rows)) == 100

    def test_grouping_by_date_is_ordered(self, populated):
        with open_database(populated) as db:
            rows = db.by_date({})
        assert len(rows) == 3
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)

    def test_grouping_by_model(self, populated):
        with open_database(populated) as db:
            rows = db.by_model({})
        assert rows[0]["model"] == "claude-opus-5"
        assert rows[0]["prompts"] == 5


class TestFilters:
    def test_project_filter(self, populated):
        with open_database(populated) as db:
            assert db.summary({"project": "data-platform"})["prompts"] == 3
            assert db.summary({"project": "AccentHRP"})["prompts"] == 1
            assert db.summary({"project": "does-not-exist"})["prompts"] == 0

    def test_date_range_filter(self, populated):
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        with open_database(populated) as db:
            assert db.summary({"date_from": today, "date_to": today})["prompts"] == 2

    def test_year_and_month_filters(self, populated):
        now = _dt.datetime.now()
        with open_database(populated) as db:
            assert db.summary({"year": now.year})["prompts"] >= 1
            assert db.summary({"year": now.year - 50})["prompts"] == 0

    def test_prompt_search_is_case_insensitive(self, populated):
        with open_database(populated) as db:
            assert db.summary({"search": "ClickHouse"})["prompts"] == 2
            assert db.summary({"search": "clickhouse"})["prompts"] == 2
            assert db.summary({"search": "CLICKHOUSE"})["prompts"] == 2

    def test_search_matches_a_substring_anywhere(self, populated):
        with open_database(populated) as db:
            assert db.summary({"search": "payroll"})["prompts"] == 1
            assert db.summary({"search": "nothing matches this"})["prompts"] == 0

    def test_filters_combine(self, populated):
        with open_database(populated) as db:
            result = db.summary({"project": "data-platform", "search": "ClickHouse"})
        assert result["prompts"] == 2

    def test_model_filter(self, populated):
        with open_database(populated) as db:
            assert db.summary({"model": "claude-opus-5"})["prompts"] == 5
            assert db.summary({"model": "claude-sonnet-5"})["prompts"] == 0


class TestPromptsPage:
    def test_pagination_walks_every_row_without_repeats(self, populated):
        seen = []
        with open_database(populated) as db:
            for page in range(1, 4):
                result = db.prompts({}, limit=2, offset=(page - 1) * 2)
                seen.extend(r["id"] for r in result["rows"])
                assert result["total"] == 5
        assert len(seen) == 5 and len(set(seen)) == 5

    def test_limit_is_clamped(self, populated):
        with open_database(populated) as db:
            assert db.prompts({}, limit=100000)["limit"] <= 1000

    def test_sorting_by_total_tokens(self, populated):
        with open_database(populated) as db:
            desc = db.prompts({}, sort="total_tokens", direction="desc")["rows"]
            asc = db.prompts({}, sort="total_tokens", direction="asc")["rows"]
        totals = [r["total_tokens"] for r in desc]
        assert totals == sorted(totals, reverse=True)
        assert [r["total_tokens"] for r in asc] == sorted(totals)

    def test_sorting_by_timestamp_and_project(self, populated):
        with open_database(populated) as db:
            stamps = [r["timestamp"] for r in db.prompts({}, sort="timestamp", direction="desc")["rows"]]
            projects = [r["project"] for r in db.prompts({}, sort="project", direction="asc")["rows"]]
        assert stamps == sorted(stamps, reverse=True)
        assert projects == sorted(projects)

    def test_an_unknown_sort_key_falls_back_safely(self, populated):
        with open_database(populated) as db:
            result = db.prompts({}, sort="'; DROP TABLE interactions; --")
        assert result["total"] == 5

    def test_cache_tokens_are_combined_for_display(self, populated):
        with open_database(populated) as db:
            row = db.prompts({}, limit=1)["rows"][0]
        assert row["cache_tokens"] == (row["cache_read_input_tokens"] or 0) + \
                                      (row["cache_creation_input_tokens"] or 0)


class TestReindex:
    def test_index_is_rebuildable_from_jsonl_alone(self, populated):
        with open_database(populated) as db:
            before = db.summary({})["total_tokens"]

        populated.index_path.unlink()

        with open_database(populated) as db:
            stats = db.reindex(populated.usage_dir, full=True)
            assert stats["records"] == 5
            assert db.summary({})["total_tokens"] == before

    def test_reindex_is_idempotent(self, populated):
        with open_database(populated) as db:
            db.reindex(populated.usage_dir, full=True)
            db.reindex(populated.usage_dir, full=True)
            assert db.summary({})["prompts"] == 5

    def test_incremental_reindex_skips_unchanged_files(self, populated):
        with open_database(populated) as db:
            db.reindex(populated.usage_dir, full=True)
            assert db.reindex(populated.usage_dir, full=False)["files"] == 0

    def test_the_highest_revision_wins(self, populated):
        """Two lines with the same id must collapse to the newer revision."""
        target = next(populated.usage_dir.rglob("prompts.jsonl"))
        records = list(iter_records(populated.usage_dir))
        first = dict(records[0])
        first["revision"] = 9
        first["usage"] = dict(first["usage"], output_tokens=999999, total_tokens=999999)

        import json
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(first) + "\n")

        with open_database(populated) as db:
            db.reindex(populated.usage_dir, full=True)
            assert db.summary({})["prompts"] == 5      # still five interactions
            row = db.prompts({"session_id": first["session_id"]}, limit=1)["rows"][0]
            assert row["output_tokens"] == 999999


class TestQuickRanges:
    def test_today_and_yesterday(self):
        today = _dt.date(2026, 9, 3)
        assert resolve_range("today", today) == ("2026-09-03", "2026-09-03")
        assert resolve_range("yesterday", today) == ("2026-09-02", "2026-09-02")

    def test_rolling_windows_are_inclusive(self):
        today = _dt.date(2026, 9, 3)
        assert resolve_range("last7", today) == ("2026-08-28", "2026-09-03")
        assert resolve_range("last30", today) == ("2026-08-05", "2026-09-03")

    def test_month_and_year_windows(self):
        today = _dt.date(2026, 9, 3)
        assert resolve_range("this_month", today) == ("2026-09-01", "2026-09-03")
        assert resolve_range("previous_month", today) == ("2026-08-01", "2026-08-31")
        assert resolve_range("this_year", today) == ("2026-01-01", "2026-09-03")

    def test_previous_month_across_a_year_boundary(self):
        assert resolve_range("previous_month", _dt.date(2026, 1, 15)) == \
            ("2025-12-01", "2025-12-31")

    def test_all_time_applies_no_bounds(self):
        assert resolve_range("all", _dt.date(2026, 9, 3)) == (None, None)

    def test_ranges_are_derived_from_the_clock_not_hard_coded(self):
        start, end = resolve_range("today")
        assert start == end == _dt.datetime.now().astimezone().strftime("%Y-%m-%d")


class TestBuildFilters:
    def test_all_means_no_filter(self):
        filters = build_filters({"project": "all", "model": "all", "year": "all"})
        assert filters == {}

    def test_explicit_dates_win_over_a_quick_range(self):
        filters = build_filters({"range": "today", "from": "2026-01-01", "to": "2026-01-31"})
        assert filters["date_from"] == "2026-01-01"
        assert filters["date_to"] == "2026-01-31"

    def test_blank_search_is_dropped(self):
        assert "search" not in build_filters({"search": "   "})
