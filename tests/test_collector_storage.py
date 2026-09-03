"""Collector, storage layout and duplicate protection (Steps 4, 5, 7, 8, 16)."""

from __future__ import annotations

import datetime as _dt
import io
import json

from tracker.collector import Collector, build_interaction, interaction_id, run_hook
from tracker.storage import dedupe_records, iter_records, known_projects, prompts_path
from tracker.utils import to_local


def _hook_payload(transcript, event="SessionEnd", prompt_id=None, cwd="D:/x"):
    payload = {
        "session_id": "sess",
        "transcript_path": str(transcript),
        "cwd": cwd,
        "hook_event_name": event,
    }
    if prompt_id:
        payload["prompt_id"] = prompt_id
    return payload


class TestDirectoryLayout:
    def test_files_land_under_year_month_day_project(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\New_Projects\data-platform", branch="fix/startup")
        builder.prompt("Fix the ClickHouse startup error", now)
        builder.reply(now + _dt.timedelta(seconds=2))
        path = builder.write()

        with Collector(config) as collector:
            records = collector.sync_transcript(path, finalize_all=True)

        assert len(records) == 1
        local = to_local(now)
        expected = (config.usage_dir / local.strftime("%Y") / local.strftime("%m")
                    / local.strftime("%d") / "data-platform" / "prompts.jsonl")
        assert expected.is_file()
        assert records[0]["project"] == "data-platform"

    def test_the_date_comes_from_the_interaction_not_from_today(self, config, make_transcript):
        moment = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=45)
        builder = make_transcript("slug", r"D:\Projects\Historic")
        builder.prompt("old prompt", moment).reply(moment + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]

        local = to_local(moment)
        assert record["date"] == local.strftime("%Y-%m-%d")
        assert record["year"] == local.year and record["month"] == local.month
        assert prompts_path(config.usage_dir, moment, "Historic").is_file()

    def test_separate_projects_get_separate_directories(self, config, make_transcript, now):
        for name in ("data-platform", "InventoryManagementSystem", "AccentHRP"):
            builder = make_transcript("slug-" + name, "D:\\New_Projects\\" + name)
            builder.prompt("hello " + name, now).reply(now + _dt.timedelta(seconds=1))
            with Collector(config) as collector:
                collector.sync_transcript(builder.write(), finalize_all=True)

        assert known_projects(config.usage_dir) == [
            "AccentHRP", "InventoryManagementSystem", "data-platform"
        ]

    def test_one_json_object_per_line(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        for i in range(3):
            at = now + _dt.timedelta(minutes=i)
            builder.prompt("prompt %d" % i, at).reply(at + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            collector.sync_transcript(builder.write(), finalize_all=True)

        target = prompts_path(config.usage_dir, now, "alpha")
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 3
        for line in lines:
            assert isinstance(json.loads(line), dict)


class TestRecordShape:
    def test_record_carries_every_documented_field(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\New_Projects\data-platform", branch="fix/clickhouse")
        builder.prompt("Fix the ClickHouse startup error", now)
        builder.reply(now + _dt.timedelta(seconds=1), model="claude-opus-5",
                      input_tokens=12500, output_tokens=3200,
                      cache_read=2100, cache_creation=0)
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]

        for key in ("id", "timestamp", "date", "year", "month", "day", "project",
                    "working_directory", "git_repository", "git_branch",
                    "session_id", "model", "prompt", "usage"):
            assert key in record, key

        assert record["git_branch"] == "fix/clickhouse"
        assert record["model"] == "claude-opus-5"
        assert record["usage"] == {
            "input_tokens": 12500, "output_tokens": 3200,
            "cache_read_input_tokens": 2100, "cache_creation_input_tokens": 0,
            "total_tokens": 17800,
        }
        assert record["usage_source"] == "claude_code_transcript"

    def test_timestamp_is_local_time_with_an_offset(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]
        assert record["timestamp"][:4].isdigit()
        assert record["timestamp"][-6] in "+-" or record["timestamp"].endswith("Z")


class TestPrivacy:
    def test_prompt_text_is_stored_by_default(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("a secret-ish prompt", now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]
        assert record["prompt"] == "a secret-ish prompt"
        assert record["prompt_length"] == len("a secret-ish prompt")
        assert record["prompt_hash"].startswith("sha256:")

    def test_disabling_storage_keeps_only_length_and_hash(self, config, make_transcript, now):
        config.data["store_prompt_text"] = False
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("a secret prompt", now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]

        assert record["prompt"] is None
        assert record["prompt_length"] == len("a secret prompt")
        assert record["prompt_hash"].startswith("sha256:")
        assert "secret" not in json.dumps(record)

    def test_long_prompts_are_truncated_to_the_configured_limit(self, config, make_transcript, now):
        config.data["prompt_text_max_chars"] = 50
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("x" * 500, now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]
        assert len(record["prompt"]) == 50
        assert record["prompt_truncated"] is True
        assert record["prompt_length"] == 500

    def test_no_credential_shaped_field_is_ever_written(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]
        keys = json.dumps(list(record.keys())).lower()
        for banned in ("token_secret", "apikey", "api_key", "password",
                       "cookie", "credential", "auth"):
            assert banned not in keys


class TestDuplicateProtection:
    def test_ids_are_stable_across_runs(self):
        turn = {"session_id": "s1", "prompt_id": "p1"}
        assert interaction_id(turn) == interaction_id(dict(turn))

    def test_different_turns_get_different_ids(self):
        assert interaction_id({"session_id": "s1", "prompt_id": "p1"}) != \
               interaction_id({"session_id": "s1", "prompt_id": "p2"})

    def test_syncing_the_same_transcript_twice_records_nothing_new(
        self, config, make_transcript, now
    ):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        path = builder.write()

        with Collector(config) as collector:
            first = collector.sync_transcript(path, finalize_all=True)
        with Collector(config) as collector:
            second = collector.sync_transcript(path, finalize_all=True)
            third = collector.sync_transcript(path, finalize_all=True)

        assert len(first) == 1 and second == [] and third == []
        assert len(list(iter_records(config.usage_dir))) == 1

    def test_a_replayed_hook_does_not_duplicate(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        path = builder.write()
        payload = _hook_payload(path)

        with Collector(config) as collector:
            collector.handle_hook(payload)
            collector.handle_hook(payload)
        with Collector(config) as collector:
            collector.handle_hook(payload)

        assert len(dedupe_records(iter_records(config.usage_dir))) == 1

    def test_a_late_flushed_message_supersedes_via_a_new_revision(
        self, config, make_transcript, now
    ):
        """Stop can fire just before the final assistant record hits disk."""
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1), output_tokens=100)
        path = builder.write()
        written = len(builder)

        with Collector(config) as collector:
            first = collector.sync_transcript(path, finalize_all=True)
        assert first[0]["revision"] == 0
        assert first[0]["usage"]["output_tokens"] == 100

        builder.reply(now + _dt.timedelta(seconds=2), output_tokens=250)
        builder.append(written)

        with Collector(config) as collector:
            second = collector.sync_transcript(path, finalize_all=True)

        assert len(second) == 1
        assert second[0]["id"] == first[0]["id"]     # same interaction
        assert second[0]["revision"] == 1            # superseding revision
        assert second[0]["usage"]["output_tokens"] == 350

        unique = dedupe_records(iter_records(config.usage_dir))
        assert len(unique) == 1
        assert unique[0]["usage"]["output_tokens"] == 350

    def test_stop_finalises_only_the_named_prompt(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("one", now).reply(now + _dt.timedelta(seconds=1))
        first_prompt_id = builder.prompt_id
        path = builder.write()

        with Collector(config) as collector:
            records = collector.sync_transcript(path, finalize_prompt_ids=[first_prompt_id])
        assert len(records) == 1 and records[0]["prompt"] == "one"

    def test_an_unfinished_turn_is_not_recorded_yet(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("in flight", now).reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            assert collector.sync_transcript(builder.write()) == []


class TestOriginFiltering:
    def test_meta_records_are_never_stored(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("system caveat", now, is_meta=True)
        builder.reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            assert collector.sync_transcript(builder.write(), finalize_all=True) == []

    def test_non_human_turns_are_tracked_but_flagged(self, config, make_transcript, now):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("task done", now, origin="task-notification")
        builder.reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            record = collector.sync_transcript(builder.write(), finalize_all=True)[0]
        assert record["origin"] == "task-notification"

    def test_non_human_turns_can_be_switched_off(self, config, make_transcript, now):
        config.data["track_non_human_turns"] = False
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("task done", now, origin="task-notification")
        builder.reply(now + _dt.timedelta(seconds=1))
        with Collector(config) as collector:
            assert collector.sync_transcript(builder.write(), finalize_all=True) == []


class TestBackfill:
    def test_imports_every_transcript_once(self, config, make_transcript, transcripts_dir, now):
        for i in range(3):
            builder = make_transcript("slug%d" % i, r"D:\Projects\proj%d" % i)
            builder.prompt("p%d" % i, now).reply(now + _dt.timedelta(seconds=1))
            builder.write()

        paths = sorted(transcripts_dir.glob("*/*.jsonl"))
        with Collector(config) as collector:
            first = collector.backfill(paths)
        with Collector(config) as collector:
            second = collector.backfill(paths)

        assert first == {"transcripts": 3, "records": 3, "skipped": 0}
        assert second["records"] == 0

    def test_a_corrupt_transcript_does_not_stop_the_run(
        self, config, make_transcript, transcripts_dir, now
    ):
        good = make_transcript("ok", r"D:\Projects\good")
        good.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        good.write()

        bad = transcripts_dir / "bad"
        bad.mkdir()
        (bad / "broken.jsonl").write_text("{not json at all\n\x00\x01", encoding="utf-8")

        with Collector(config) as collector:
            stats = collector.backfill(sorted(transcripts_dir.glob("*/*.jsonl")))
        assert stats["records"] == 1


class TestHookRobustness:
    """Step 16: a broken tracker must never take Claude Code down."""

    def test_hook_exits_zero_on_garbage_input(self, config):
        assert run_hook(io.StringIO("this is not json")) == 0

    def test_hook_exits_zero_on_empty_input(self, config):
        assert run_hook(io.StringIO("")) == 0

    def test_hook_exits_zero_when_the_transcript_is_missing(self, config, tmp_path):
        payload = json.dumps(_hook_payload(tmp_path / "nope.jsonl"))
        assert run_hook(io.StringIO(payload)) == 0

    def test_hook_exits_zero_when_the_payload_has_no_transcript(self, config):
        assert run_hook(io.StringIO('{"hook_event_name": "Stop"}')) == 0

    def test_hook_exits_zero_when_storage_is_unwritable(
        self, config, make_transcript, now, monkeypatch
    ):
        builder = make_transcript("slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1))
        payload = json.dumps(_hook_payload(builder.write()))

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("tracker.collector.append_records", explode)
        assert run_hook(io.StringIO(payload)) == 0

    def test_hook_writes_an_empty_json_object_to_stdout(self, config, capsys):
        run_hook(io.StringIO(""))
        assert capsys.readouterr().out == "{}"

    def test_problems_are_logged_rather_than_raised(self, config):
        # A payload with no transcript_path is a condition worth logging.
        run_hook(io.StringIO('{"hook_event_name": "Stop", "session_id": "s"}'))
        log = config.logs_dir / "tracker.log"
        assert log.exists()
        assert "transcript_path" in log.read_text(encoding="utf-8")


class TestBuildInteraction:
    def test_a_turn_without_a_timestamp_is_skipped(self, config):
        turn = {"session_id": "s", "prompt_id": "p", "usage": {}, "models": {}}
        assert build_interaction(turn, "proj", None, config) is None
