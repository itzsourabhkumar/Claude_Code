"""Dashboard API, exports and reports (Steps 11, 12, 13, 14).

The server is started on an ephemeral loopback port against an isolated data
root, exercised over real HTTP, then shut down.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from tracker.collector import Collector


@pytest.fixture
def seeded(config, make_transcript):
    base = _dt.datetime.now(_dt.timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    plan = [
        ("data-platform", "Fix the ClickHouse startup error", 0, 900),
        ("data-platform", "Refactor the ingestion job", 1, 300),
        ("AccentHRP", "Add payroll export", 1, 500),
    ]
    for project, prompt, offset, out in plan:
        moment = base - _dt.timedelta(days=offset)
        builder = make_transcript("slug-%s-%d" % (project, out), "D:\\Projects\\" + project)
        builder.prompt(prompt, moment)
        builder.reply(moment + _dt.timedelta(seconds=1), output_tokens=out,
                      input_tokens=5, cache_read=1000, cache_creation=50)
        with Collector(config) as collector:
            collector.sync_transcript(builder.write(), finalize_all=True)
    return config


@pytest.fixture
def api(seeded, monkeypatch):
    """A live dashboard server on 127.0.0.1, torn down after the test."""
    import server as server_module

    monkeypatch.setattr(server_module, "STATE", server_module._State(seeded))
    with server_module.STATE.lock:
        server_module.STATE.db.reindex(seeded.usage_dir, full=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]

    def get(path):
        with urlopen(base + path, timeout=10) as response:
            return response.status, response.headers, response.read()

    def get_json(path):
        return json.loads(get(path)[2].decode("utf-8"))

    get.json = get_json
    get.base = base
    try:
        yield get
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.STATE.close()


class TestBinding:
    def test_the_server_only_ever_binds_to_loopback(self):
        import server as server_module

        assert server_module.BIND_HOST == "127.0.0.1"


class TestEndpoints:
    def test_summary(self, api):
        data = api.json("/api/summary")
        assert data["prompts"] == 3
        assert data["projects"] == 2
        assert data["output_tokens"] == 900 + 300 + 500
        assert "generated_at" in data

    def test_projects_with_percentages(self, api):
        rows = api.json("/api/projects")["rows"]
        assert {r["project"] for r in rows} == {"data-platform", "AccentHRP"}
        assert round(sum(r["pct"] for r in rows)) == 100

    def test_usage_series_is_ordered_by_date(self, api):
        rows = api.json("/api/usage")["rows"]
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)

    def test_models_endpoint(self, api):
        rows = api.json("/api/models")["rows"]
        assert rows[0]["model"] == "claude-opus-5"

    def test_prompts_are_paginated(self, api):
        data = api.json("/api/prompts?per_page=2&page=1")
        assert data["total"] == 3 and data["pages"] == 2 and len(data["rows"]) == 2

        page2 = api.json("/api/prompts?per_page=2&page=2")
        assert len(page2["rows"]) == 1
        assert not {r["id"] for r in data["rows"]} & {r["id"] for r in page2["rows"]}

    def test_prompt_rows_carry_the_history_columns(self, api):
        row = api.json("/api/prompts?per_page=1")["rows"][0]
        for key in ("timestamp", "project", "git_branch", "model", "prompt",
                    "input_tokens", "output_tokens", "cache_tokens", "total_tokens"):
            assert key in row, key

    def test_filters_endpoint_is_populated_from_the_data(self, api):
        data = api.json("/api/filters")
        assert sorted(data["projects"]) == ["AccentHRP", "data-platform"]
        assert data["models"] == ["claude-opus-5"]
        assert data["years"] == [_dt.datetime.now().year]

    def test_meta_endpoint(self, api):
        data = api.json("/api/meta")
        assert data["total_interactions"] == 3
        assert data["store_prompt_text"] is True

    def test_refresh_reindexes(self, api):
        assert "reindexed" in api.json("/api/refresh")

    def test_unknown_endpoint_returns_404(self, api):
        with pytest.raises(HTTPError) as excinfo:
            api("/api/nope")
        assert excinfo.value.code == 404


class TestApiFiltering:
    def test_project_filter(self, api):
        assert api.json("/api/summary?project=data-platform")["prompts"] == 2
        assert api.json("/api/summary?project=AccentHRP")["prompts"] == 1

    def test_all_projects_means_everything(self, api):
        assert api.json("/api/summary?project=all")["prompts"] == 3

    def test_date_filter(self, api):
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        assert api.json("/api/summary?from=%s&to=%s" % (today, today))["prompts"] == 1

    def test_quick_range_filter(self, api):
        assert api.json("/api/summary?range=today")["prompts"] == 1
        assert api.json("/api/summary?range=last7")["prompts"] == 3

    def test_search_filter_is_case_insensitive(self, api):
        assert api.json("/api/prompts?search=clickhouse")["total"] == 1
        assert api.json("/api/prompts?search=CLICKHOUSE")["total"] == 1
        assert api.json("/api/prompts?search=payroll")["total"] == 1
        assert api.json("/api/prompts?search=zzzz")["total"] == 0

    def test_sorting_is_applied(self, api):
        rows = api.json("/api/prompts?sort=total_tokens&dir=desc")["rows"]
        totals = [r["total_tokens"] for r in rows]
        assert totals == sorted(totals, reverse=True)


class TestExports:
    def test_csv_export_respects_the_active_filter(self, api):
        status, headers, body = api("/api/export.csv?project=AccentHRP")
        assert status == 200
        assert "text/csv" in headers["Content-Type"]
        assert "attachment" in headers["Content-Disposition"]

        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        assert len(rows) == 1
        assert rows[0]["project"] == "AccentHRP"
        assert rows[0]["output_tokens"] == "500"

    def test_csv_export_of_everything(self, api):
        rows = list(csv.DictReader(io.StringIO(api("/api/export.csv")[2].decode("utf-8-sig"))))
        assert len(rows) == 3

    def test_json_export_respects_the_active_filter(self, api):
        data = json.loads(api("/api/export.json?search=ClickHouse")[2].decode("utf-8"))
        assert data["count"] == 1
        assert data["filters"] == {"search": "ClickHouse"}
        assert data["interactions"][0]["usage"]["output_tokens"] == 900

    def test_json_export_records_are_the_full_stored_shape(self, api):
        record = json.loads(api("/api/export.json")[2].decode("utf-8"))["interactions"][0]
        for key in ("id", "timestamp", "project", "session_id", "usage", "usage_source"):
            assert key in record


class TestStatic:
    def test_dashboard_is_served(self, api):
        status, headers, body = api("/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert b"Claude Code Token Usage" in body

    def test_css_and_js_are_served(self, api):
        assert api("/css/dashboard.css")[0] == 200
        assert api("/js/dashboard.js")[0] == 200

    def test_path_traversal_is_refused(self, api):
        for attempt in ("/../config.json", "/%2e%2e/config.json", "/../../config.json"):
            with pytest.raises(HTTPError) as excinfo:
                api(attempt)
            assert excinfo.value.code in (403, 404)
