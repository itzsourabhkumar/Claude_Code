"""Transcript parsing and token extraction (Steps 5 and 6)."""

from __future__ import annotations

import datetime as _dt

from tracker.parser import (
    TranscriptParser,
    classify_origin,
    extract_prompt_text,
    parse_full,
    primary_model,
    turn_signature,
)
from tracker.token_parser import (
    add_usage,
    cache_tokens,
    empty_usage,
    extract_usage,
    finalize_usage,
    total_tokens,
)


class TestTokenExtraction:
    def test_reads_the_four_real_dimensions(self):
        usage = extract_usage({"usage": {
            "input_tokens": 12500, "output_tokens": 3200,
            "cache_read_input_tokens": 2100, "cache_creation_input_tokens": 0,
        }})
        assert usage == {
            "input_tokens": 12500, "output_tokens": 3200,
            "cache_read_input_tokens": 2100, "cache_creation_input_tokens": 0,
        }

    def test_missing_usage_block_returns_none_not_zero(self):
        assert extract_usage({"model": "claude-opus-5"}) is None
        assert extract_usage({"usage": {}}) is None
        assert extract_usage(None) is None

    def test_absent_field_stays_none_rather_than_being_invented(self):
        usage = extract_usage({"usage": {"input_tokens": 10, "output_tokens": 5}})
        assert usage["cache_read_input_tokens"] is None
        assert usage["cache_creation_input_tokens"] is None

    def test_total_is_the_sum_of_known_dimensions(self):
        usage = {"input_tokens": 12500, "output_tokens": 3200,
                 "cache_read_input_tokens": 2100, "cache_creation_input_tokens": 0}
        assert total_tokens(usage) == 12500 + 3200 + 2100 + 0

    def test_total_of_an_entirely_unknown_usage_is_none(self):
        assert total_tokens(empty_usage()) is None

    def test_accumulation_across_messages(self):
        running = empty_usage()
        add_usage(running, {"input_tokens": 2, "output_tokens": 100,
                            "cache_read_input_tokens": 500, "cache_creation_input_tokens": 10})
        add_usage(running, {"input_tokens": 3, "output_tokens": 50,
                            "cache_read_input_tokens": 700, "cache_creation_input_tokens": 0})
        assert running == {"input_tokens": 5, "output_tokens": 150,
                           "cache_read_input_tokens": 1200, "cache_creation_input_tokens": 10}

    def test_a_dimension_nobody_reported_stays_unknown(self):
        running = empty_usage()
        add_usage(running, {"input_tokens": 5, "output_tokens": 1,
                            "cache_read_input_tokens": None, "cache_creation_input_tokens": None})
        assert running["cache_read_input_tokens"] is None
        assert cache_tokens(running) is None

    def test_finalize_adds_total(self):
        result = finalize_usage({"input_tokens": 1, "output_tokens": 2,
                                 "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4})
        assert result["total_tokens"] == 10

    def test_negative_and_junk_values_are_rejected(self):
        usage = extract_usage({"usage": {"input_tokens": -5, "output_tokens": "abc",
                                         "cache_read_input_tokens": 7}})
        assert usage["input_tokens"] is None
        assert usage["output_tokens"] is None
        assert usage["cache_read_input_tokens"] == 7


class TestPromptExtraction:
    def test_plain_string_content(self):
        assert extract_prompt_text({"content": "hello"}) == ("hello", False)

    def test_text_blocks_are_joined(self):
        message = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        assert extract_prompt_text(message) == ("a\nb", False)

    def test_tool_results_are_flagged_and_never_treated_as_prompts(self):
        message = {"content": [{"type": "tool_result", "content": "output"}]}
        assert extract_prompt_text(message) == (None, True)


class TestOriginClassification:
    def test_human_prompt(self):
        assert classify_origin({"origin": {"kind": "human"}, "promptSource": "typed"}) == \
            ("human", "typed")

    def test_task_notification_is_not_human(self):
        origin, _ = classify_origin({"origin": {"kind": "task-notification"}})
        assert origin == "task-notification"

    def test_meta_records_are_marked(self):
        assert classify_origin({"isMeta": True})[0] == "meta"


class TestTurnGrouping:
    def test_one_prompt_and_its_replies_form_one_turn(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("first prompt", now)
        builder.reply(now + _dt.timedelta(seconds=2), output_tokens=100, cache_read=1000)
        builder.tool_result(now + _dt.timedelta(seconds=3))
        builder.reply(now + _dt.timedelta(seconds=4), output_tokens=50, cache_read=1200)
        path = builder.write()

        turns = parse_full(path)
        assert len(turns) == 1
        turn = turns[0]
        assert turn["prompt"] == "first prompt"
        assert turn["assistant_messages"] == 2
        assert turn["usage"]["output_tokens"] == 150
        assert turn["usage"]["cache_read_input_tokens"] == 2200

    def test_a_new_prompt_starts_a_new_turn(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("one", now).reply(now + _dt.timedelta(seconds=1), output_tokens=10)
        builder.prompt("two", now + _dt.timedelta(minutes=1))
        builder.reply(now + _dt.timedelta(minutes=1, seconds=1), output_tokens=20)
        turns = parse_full(builder.write())

        assert [t["prompt"] for t in turns] == ["one", "two"]
        assert [t["usage"]["output_tokens"] for t in turns] == [10, 20]

    def test_metadata_records_are_ignored(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.noise(now)
        builder.prompt("real", now).reply(now + _dt.timedelta(seconds=1))
        builder.noise(now + _dt.timedelta(seconds=2))
        turns = parse_full(builder.write())
        assert len(turns) == 1 and turns[0]["prompt"] == "real"

    def test_iterations_do_not_double_count(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1),
                                       input_tokens=4, output_tokens=620,
                                       cache_read=57862, cache_creation=8248)
        turn = parse_full(builder.write())[0]
        assert turn["usage"]["output_tokens"] == 620
        assert total_tokens(turn["usage"]) == 4 + 620 + 57862 + 8248

    def test_repeated_message_id_is_counted_once(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("p", now)
        builder.reply(now + _dt.timedelta(seconds=1), output_tokens=10, message_id="msg_same")
        builder.reply(now + _dt.timedelta(seconds=2), output_tokens=10, message_id="msg_same")
        turn = parse_full(builder.write())[0]
        assert turn["assistant_messages"] == 1
        assert turn["usage"]["output_tokens"] == 10

    def test_primary_model_is_the_one_producing_most_output(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("p", now)
        builder.reply(now + _dt.timedelta(seconds=1), model="claude-sonnet-5", output_tokens=10)
        builder.reply(now + _dt.timedelta(seconds=2), model="claude-opus-5", output_tokens=900)
        turn = parse_full(builder.write())[0]
        assert primary_model(turn) == "claude-opus-5"
        assert sorted(turn["models"]) == ["claude-opus-5", "claude-sonnet-5"]

    def test_a_prompt_with_no_reply_records_unknown_tokens(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("aborted", now)
        turn = parse_full(builder.write())[0]
        assert total_tokens(turn["usage"]) is None


class TestIncrementalParsing:
    def test_only_new_bytes_are_re_read(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("one", now).reply(now + _dt.timedelta(seconds=1), output_tokens=10)
        path = builder.write()
        written = len(builder)

        parser = TranscriptParser(path)
        closed, state = parser.parse()
        assert closed == [] and state["open_turn"]["prompt"] == "one"
        first_offset = state["offset"]
        assert first_offset > 0

        builder.prompt("two", now + _dt.timedelta(minutes=1))
        builder.reply(now + _dt.timedelta(minutes=1, seconds=1), output_tokens=20)
        builder.append(written)

        parser2 = TranscriptParser(path, state)
        closed2, state2 = parser2.parse()
        assert [t["prompt"] for t in closed2] == ["one"]
        assert state2["open_turn"]["prompt"] == "two"
        assert state2["offset"] > first_offset

    def test_a_partially_written_line_is_left_for_next_time(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("one", now).reply(now + _dt.timedelta(seconds=1), output_tokens=10)
        path = builder.write()
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "assistant", "message": {"usa')  # torn write

        parser = TranscriptParser(path)
        _closed, state = parser.parse()
        assert state["open_turn"]["usage"]["output_tokens"] == 10

    def test_a_truncated_file_restarts_cleanly(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("one", now).reply(now + _dt.timedelta(seconds=1))
        path = builder.write()
        parser = TranscriptParser(path, {"offset": 10_000_000, "open_turn": None})
        _closed, state = parser.parse()
        assert state["open_turn"] is not None


class TestSignature:
    def test_signature_changes_when_a_turn_grows(self, make_transcript, now):
        builder = make_transcript("proj-slug", r"D:\Projects\alpha")
        builder.prompt("p", now).reply(now + _dt.timedelta(seconds=1), output_tokens=10)
        first = turn_signature(parse_full(builder.write())[0])

        builder.reply(now + _dt.timedelta(seconds=2), output_tokens=10)
        second = turn_signature(parse_full(builder.write())[0])
        assert first != second
