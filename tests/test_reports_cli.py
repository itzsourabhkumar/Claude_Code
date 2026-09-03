"""Report generation and the CLI (Steps 13 and 14)."""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from tracker import cli
from tracker.collector import Collector
from tracker.database import open_database
from tracker.reports import daily_report, generate_all, monthly_report, yearly_report


@pytest.fixture
def seeded(config, make_transcript):
    base = _dt.datetime.now(_dt.timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    for project, offset, out in (("data-platform", 0, 400), ("AccentHRP", 0, 200),
                                 ("ReviewMint", 1, 100)):
        moment = base - _dt.timedelta(days=offset)
        builder = make_transcript("slug-%s" % project, "D:\\Projects\\" + project)
        builder.prompt("work on " + project, moment)
        builder.reply(moment + _dt.timedelta(seconds=1), output_tokens=out,
                      input_tokens=10, cache_read=500, cache_creation=25)
        with Collector(config) as collector:
            collector.sync_transcript(builder.write(), finalize_all=True)
    with open_database(config) as db:
        db.reindex(config.usage_dir, full=True)
    return config


class TestReports:
    def test_daily_report_shape(self, seeded):
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        with open_database(seeded) as db:
            report = daily_report(db, today)

        assert report["date"] == today
        assert report["total_prompts"] == 2
        assert report["output_tokens"] == 600
        assert report["cache_tokens"] == 1050
        assert set(report["projects"]) == {"data-platform", "AccentHRP"}
        assert report["projects"]["data-platform"] > report["projects"]["AccentHRP"]

    def test_monthly_report_lists_active_days(self, seeded):
        now = _dt.datetime.now()
        with open_database(seeded) as db:
            report = monthly_report(db, now.year, now.month)
        assert report["month"] == now.strftime("%Y-%m")
        assert report["active_days"] >= 1
        assert report["total_prompts"] >= 2

    def test_yearly_report_lists_months(self, seeded):
        year = _dt.datetime.now().year
        with open_database(seeded) as db:
            report = yearly_report(db, year)
        assert report["year"] == str(year)
        assert report["active_months"] >= 1
        assert report["total_tokens"] > 0

    def test_generate_all_writes_files_for_every_period_present(self, seeded):
        with open_database(seeded) as db:
            written = generate_all(db, seeded)

        assert written["daily"] >= 1 and written["monthly"] >= 1 and written["yearly"] >= 1
        daily = sorted((seeded.reports_dir / "daily").glob("*.json"))
        assert daily, "no daily reports written"

        report = json.loads(daily[-1].read_text(encoding="utf-8"))
        assert "total_tokens" in report and "projects" in report

    def test_report_filenames_are_derived_from_the_data(self, seeded):
        with open_database(seeded) as db:
            generate_all(db, seeded)
        names = {p.stem for p in (seeded.reports_dir / "daily").glob("*.json")}
        expected = _dt.datetime.now().strftime("%Y-%m-%d")
        assert expected in names

    def test_regeneration_is_idempotent(self, seeded):
        with open_database(seeded) as db:
            first = generate_all(db, seeded)
            second = generate_all(db, seeded)
        assert first == second


class TestCli:
    def test_today(self, seeded, capsys):
        assert cli.main(["today"]) == 0
        out = capsys.readouterr().out
        assert "Claude Code Usage" in out
        assert "Prompts" in out and "Total Tokens" in out
        assert "data-platform" in out

    def test_yesterday(self, seeded, capsys):
        assert cli.main(["yesterday"]) == 0
        assert "ReviewMint" in capsys.readouterr().out

    def test_month(self, seeded, capsys):
        assert cli.main(["month"]) == 0
        assert "Prompts" in capsys.readouterr().out

    def test_year(self, seeded, capsys):
        assert cli.main(["year"]) == 0
        assert "Total Tokens" in capsys.readouterr().out

    def test_project(self, seeded, capsys):
        assert cli.main(["project", "data-platform"]) == 0
        out = capsys.readouterr().out
        assert "data-platform" in out and "By date:" in out

    def test_projects_table(self, seeded, capsys):
        assert cli.main(["projects"]) == 0
        out = capsys.readouterr().out
        assert "Project" in out and "data-platform" in out and "AccentHRP" in out

    def test_search(self, seeded, capsys):
        assert cli.main(["search", "AccentHRP"]) == 0
        assert "1 interaction(s)" in capsys.readouterr().out

    def test_search_is_case_insensitive(self, seeded, capsys):
        cli.main(["search", "accenthrp"])
        assert "1 interaction(s)" in capsys.readouterr().out

    def test_range(self, seeded, capsys):
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        assert cli.main(["range", "--from", today, "--to", today]) == 0
        assert "Prompts" in capsys.readouterr().out

    def test_report(self, seeded, capsys):
        assert cli.main(["report"]) == 0
        assert "Reports written to" in capsys.readouterr().out
        assert list((seeded.reports_dir / "daily").glob("*.json"))

    def test_reindex(self, seeded, capsys):
        assert cli.main(["reindex"]) == 0
        assert "Indexed" in capsys.readouterr().out

    def test_status(self, seeded, capsys):
        assert cli.main(["status"]) == 0
        out = capsys.readouterr().out
        assert "Claude Code Token Usage Tracker" in out
        assert "interactions" in out and "dashboard" in out

    def test_json_output_is_machine_readable(self, seeded, capsys):
        assert cli.main(["json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["prompts"] == 3
        assert len(payload["projects"]) == 3

    def test_json_respects_filters(self, seeded, capsys):
        cli.main(["json", "--project", "data-platform"])
        assert json.loads(capsys.readouterr().out)["summary"]["prompts"] == 1

    def test_backfill_imports_transcripts(self, config, transcripts_dir, make_transcript, capsys):
        moment = _dt.datetime.now(_dt.timezone.utc)
        builder = make_transcript("slug", r"D:\Projects\Imported")
        builder.prompt("hello", moment).reply(moment + _dt.timedelta(seconds=1))
        builder.write()

        assert cli.main(["backfill", "--projects-dir", str(transcripts_dir)]) == 0
        assert "Imported 1 new interaction" in capsys.readouterr().out

        assert cli.main(["backfill", "--projects-dir", str(transcripts_dir)]) == 0
        assert "Imported 0 new interaction" in capsys.readouterr().out

    def test_empty_period_does_not_crash(self, config, capsys):
        assert cli.main(["today"]) == 0
        assert "No usage recorded" in capsys.readouterr().out
