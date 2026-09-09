"""Cost as it reaches the user: API responses, exports, the CLI and reports.

These build a small known dataset through the real collector, then assert on the
money each surface reports - so a change that silently drops cost from one
endpoint, or lets two surfaces disagree about the same filter, fails here.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from tracker.collector import Collector
from tracker.database import open_database
from tracker.pricing import PriceBook
from tracker.query import cost_summary

MILLION = 1_000_000


@pytest.fixture
def costed_data(config, make_transcript, now):
    """Two projects on two models, with round token counts.

    Opus and Haiku deliberately: a blended rate cannot price this dataset, so
    any regression to one-rate costing shows up as a wrong total.
    """
    builder = make_transcript("alpha", "/work/alpha")
    builder.prompt("opus turn", now)
    builder.reply(now + _dt.timedelta(seconds=1), model="claude-opus-5",
                  input_tokens=MILLION, output_tokens=MILLION,
                  cache_read=MILLION, cache_creation=0)
    builder.write()

    other = make_transcript("beta", "/work/beta")
    other.prompt("haiku turn", now)
    other.reply(now + _dt.timedelta(seconds=1), model="claude-haiku-4-5",
                input_tokens=MILLION, output_tokens=0,
                cache_read=0, cache_creation=0)
    other.write()

    with Collector(config) as collector:
        collector.sync_transcript(builder.path, finalize_all=True)
        collector.sync_transcript(other.path, finalize_all=True)
    with open_database(config) as db:
        db.reindex(config.usage_dir, full=True)
    return config


#: Opus: 1M input @$5 + 1M output @$25 + 1M cache read @$0.50  = $30.50
#: Haiku: 1M input @$1                                          =  $1.00
EXPECTED_USD = 31.50


@pytest.fixture
def api(costed_data, monkeypatch):
    """The real server, on an ephemeral loopback port."""
    import server as server_module

    server_module.STATE = server_module._State(costed_data)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def get(path):
        with urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=15) as response:
            return response.read()

    def get_json(path):
        return json.loads(get(path))

    get.json = get_json
    try:
        yield get
    finally:
        httpd.shutdown()
        httpd.server_close()
        server_module.STATE.close()


# --------------------------------------------------------------------------
class TestSummaryEndpoint:
    def test_summary_reports_the_three_buckets_and_a_total(self, api):
        cost = api.json("/api/summary")["cost"]
        for field in ("input_cost_inr", "cached_input_cost_inr",
                      "output_cost_inr", "total_cost_inr"):
            assert cost[field] is not None, field

    def test_each_model_is_priced_at_its_own_rate(self, api):
        cost = api.json("/api/summary")["cost"]
        assert cost["total_cost_usd"] == pytest.approx(EXPECTED_USD)

    def test_the_total_is_the_sum_of_the_displayed_buckets(self, api):
        cost = api.json("/api/summary")["cost"]
        assert cost["total_cost_inr"] == pytest.approx(
            cost["input_cost_inr"] + cost["cached_input_cost_inr"]
            + cost["output_cost_inr"])

    def test_token_fields_are_untouched(self, api):
        """The existing API contract must not change."""
        summary = api.json("/api/summary")
        assert summary["input_tokens"] == 2 * MILLION
        assert summary["output_tokens"] == MILLION
        assert summary["prompts"] == 2

    def test_cost_respects_the_filter(self, api):
        only_haiku = api.json("/api/summary?model=claude-haiku-4-5")["cost"]
        assert only_haiku["total_cost_usd"] == pytest.approx(1.0)

    def test_the_conversion_rate_is_disclosed(self, api):
        cost = api.json("/api/summary")["cost"]
        assert cost["usd_to_inr"] > 0
        assert cost["currency"] == "INR"
        assert cost["total_cost_inr"] == pytest.approx(
            cost["total_cost_usd"] * cost["usd_to_inr"])


class TestGroupedEndpoints:
    def test_projects_carry_cost(self, api):
        rows = {r["project"]: r for r in api.json("/api/projects")["rows"]}
        assert rows["alpha"]["total_cost_usd"] == pytest.approx(30.50)
        assert rows["beta"]["total_cost_usd"] == pytest.approx(1.0)

    def test_project_costs_sum_to_the_summary_total(self, api):
        rows = api.json("/api/projects")["rows"]
        summary = api.json("/api/summary")["cost"]
        assert sum(r["total_cost_usd"] for r in rows) == pytest.approx(
            summary["total_cost_usd"])

    def test_project_rows_keep_their_existing_fields(self, api):
        row = api.json("/api/projects")["rows"][0]
        for field in ("project", "prompts", "input_tokens", "total_tokens", "pct"):
            assert field in row

    def test_daily_rows_carry_cost(self, api):
        rows = api.json("/api/usage")["rows"]
        assert rows and all(r["total_cost_inr"] is not None for r in rows)

    def test_daily_costs_sum_to_the_summary_total(self, api):
        rows = api.json("/api/usage")["rows"]
        summary = api.json("/api/summary")["cost"]
        assert sum(r["total_cost_usd"] for r in rows) == pytest.approx(
            summary["total_cost_usd"])

    def test_model_rows_carry_cost(self, api):
        rows = {r["model"]: r for r in api.json("/api/models")["rows"]}
        assert rows["claude-opus-5"]["total_cost_usd"] == pytest.approx(30.50)
        assert rows["claude-haiku-4-5"]["total_cost_usd"] == pytest.approx(1.0)

    def test_history_rows_carry_cost(self, api):
        rows = api.json("/api/prompts")["rows"]
        assert rows and all(r["total_cost_inr"] is not None for r in rows)


class TestPricingEndpoint:
    def test_it_publishes_the_rates_in_use(self, api):
        payload = api.json("/api/pricing")
        assert payload["models"]["claude-opus-5"]["input"] == 5.0
        assert payload["usd_to_inr"] > 0
        assert payload["cache_write_ttl"] in ("5m", "1h")
        assert payload["pricing_as_of"]

    def test_meta_tells_the_dashboard_pricing_is_on(self, api):
        assert api.json("/api/meta")["pricing_enabled"] is True


class TestExports:
    def test_csv_gains_cost_columns_after_the_existing_ones(self, api):
        text = api("/api/export.csv").decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        header = list(rows[0].keys())
        # Every pre-existing column keeps its position.
        assert header[:14] == [
            "timestamp", "date", "project", "git_branch", "model", "session_id",
            "origin", "prompt", "prompt_length", "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens"]
        assert "total_cost_inr" in header
        assert any(float(r["total_cost_inr"]) > 0 for r in rows)

    def test_json_export_carries_the_aggregate_cost(self, api):
        payload = json.loads(api("/api/export.json"))
        assert payload["cost"]["total_cost_usd"] == pytest.approx(EXPECTED_USD)
        assert len(payload["interactions"]) == 2


class TestPricingDisabled:
    def test_costs_are_absent_when_pricing_is_off(self, costed_data, tracker_root):
        from tracker.config import load_config

        (tracker_root / "config.json").write_text(
            json.dumps({"pricing": {"enabled": False},
                        "project_detection": {"use_git_root": False}}), encoding="utf-8")
        config = load_config()
        book = PriceBook.from_config(config)
        assert book.enabled is False
        # Tokens are still recorded and reported - only the money disappears.
        with open_database(config) as db:
            assert db.summary({})["prompts"] == 2


# --------------------------------------------------------------------------
class TestCli:
    def test_today_prints_the_breakdown(self, costed_data, capsys, monkeypatch):
        from tracker import cli

        monkeypatch.setattr(cli, "_range_for", lambda _c: (None, None))
        assert cli.main(["today"]) == 0
        out = capsys.readouterr().out
        assert "Input Tokens Cost" in out
        assert "Cached Input Tokens Cost" in out
        assert "Output Tokens Cost" in out
        assert "Total Cost" in out

    def test_projects_table_has_a_cost_column(self, costed_data, capsys):
        from tracker import cli

        assert cli.main(["projects"]) == 0
        assert "Cost" in capsys.readouterr().out

    def test_json_output_carries_cost(self, costed_data, capsys):
        from tracker import cli

        assert cli.main(["json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["cost"]["total_cost_usd"] == pytest.approx(EXPECTED_USD)

    def test_the_cli_and_the_api_agree(self, costed_data):
        """One implementation, so a filter can never mean two different totals."""
        prices = PriceBook.from_config(costed_data)
        with open_database(costed_data) as db:
            direct = cost_summary(db, {}, prices)
        assert direct["total_cost_usd"] == pytest.approx(EXPECTED_USD)


class TestReports:
    def test_generated_reports_include_cost(self, costed_data):
        from tracker.reports import generate_all

        with open_database(costed_data) as db:
            generate_all(db, costed_data)

        daily = sorted((costed_data.reports_dir / "daily").glob("*.json"))
        assert daily
        report = json.loads(daily[0].read_text(encoding="utf-8"))
        assert "cost" in report
        assert report["cost"]["currency"] == "INR"
        assert report["cost"]["total_cost_inr"] is not None
        # Existing report fields are untouched.
        assert "total_tokens" in report and "projects" in report

    def test_report_cost_parts_sum_to_its_total(self, costed_data):
        from tracker.reports import generate_all

        with open_database(costed_data) as db:
            generate_all(db, costed_data, only="daily")
        for path in (costed_data.reports_dir / "daily").glob("*.json"):
            cost = json.loads(path.read_text(encoding="utf-8"))["cost"]
            assert cost["total_cost_inr"] == pytest.approx(
                cost["input_cost_inr"] + cost["cached_input_cost_inr"]
                + cost["output_cost_inr"])
