"""Shared fixtures.

Every test runs against an isolated ``CCTRACKER_ROOT`` under pytest's tmp_path,
so nothing here can read or write the user's real usage data, and no test ever
touches the real ``~/.claude/settings.json``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------
# Transcript building - mirrors the real Claude Code transcript format,
# captured from Claude Code 2.1.252 (see README, "How token counts are
# obtained").
# --------------------------------------------------------------------------
def _stamp(moment: _dt.datetime) -> str:
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TranscriptBuilder:
    """Builds a realistic session transcript one record at a time."""

    def __init__(self, path: Path, cwd: str, branch: str = "main",
                 session_id: str | None = None, version: str = "2.1.252"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cwd = cwd
        self.branch = branch
        self.session_id = session_id or str(uuid.uuid4())
        self.version = version
        self.prompt_id: str | None = None
        self._records: list = []

    # -- record helpers ---------------------------------------------------
    def _base(self, kind: str, moment: _dt.datetime) -> dict:
        return {
            "type": kind,
            "uuid": str(uuid.uuid4()),
            "timestamp": _stamp(moment),
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "gitBranch": self.branch,
            "version": self.version,
            "entrypoint": "cli",
            "isSidechain": False,
            "userType": "external",
        }

    def prompt(self, text: str, moment: _dt.datetime, origin: str = "human",
               prompt_source: str = "typed", is_meta: bool = False):
        """A user prompt - this is what starts a new turn."""
        self.prompt_id = str(uuid.uuid4())
        record = self._base("user", moment)
        record.update({
            "promptId": self.prompt_id,
            "promptSource": prompt_source,
            "origin": {"kind": origin},
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        })
        if is_meta:
            record["isMeta"] = True
        self._records.append(record)
        return self

    def tool_result(self, moment: _dt.datetime, output: str = "ok"):
        """Tool output fed back to the model - part of the current turn."""
        record = self._base("user", moment)
        record.update({
            "promptId": self.prompt_id,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_" + uuid.uuid4().hex[:8],
                 "content": output}
            ]},
        })
        self._records.append(record)
        return self

    def reply(self, moment: _dt.datetime, model: str = "claude-opus-5",
              input_tokens: int = 4, output_tokens: int = 100,
              cache_read: int = 1000, cache_creation: int = 200,
              message_id: str | None = None, usage: bool = True):
        """An assistant message carrying the API's real usage object."""
        record = self._base("assistant", moment)
        message = {
            "model": model,
            "id": message_id or ("msg_" + uuid.uuid4().hex[:16]),
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "reply"}],
        }
        if usage:
            message["usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                # The parser must ignore `iterations` - it double-counts.
                "iterations": [{
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                }],
            }
        record["message"] = message
        self._records.append(record)
        return self

    def noise(self, moment: _dt.datetime):
        """Metadata records the tracker must ignore."""
        for kind in ("attachment", "ai-title", "file-history-snapshot", "last-prompt"):
            record = self._base(kind, moment)
            record.pop("message", None)
            self._records.append(record)
        return self

    # -- output -----------------------------------------------------------
    def write(self) -> Path:
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in self._records:
                handle.write(json.dumps(record) + "\n")
        return self.path

    def append(self, since: int) -> Path:
        """Append only records added after index ``since`` (simulates a live session)."""
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in self._records[since:]:
                handle.write(json.dumps(record) + "\n")
        return self.path

    def __len__(self) -> int:
        return len(self._records)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def tracker_root(tmp_path, monkeypatch):
    """An isolated tracker installation rooted in tmp_path."""
    root = tmp_path / "tracker_root"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({
        "store_prompt_text": True,
        "paths": {
            "usage_dir": "data/usage",
            "state_dir": "data/state",
            "index_path": "data/index.sqlite3",
            "reports_dir": "reports",
            "logs_dir": "logs",
        },
        "project_detection": {"use_git_root": False},
    }), encoding="utf-8")
    monkeypatch.setenv("CCTRACKER_ROOT", str(root))
    return root


@pytest.fixture
def config(tracker_root):
    from tracker.config import load_config

    return load_config()


@pytest.fixture
def transcripts_dir(tmp_path):
    path = tmp_path / "claude_projects"
    path.mkdir()
    return path


@pytest.fixture
def make_transcript(transcripts_dir):
    """Factory returning a TranscriptBuilder writing under a fake projects dir."""

    def _make(project_dir_name: str, cwd: str, **kwargs) -> TranscriptBuilder:
        slug = transcripts_dir / project_dir_name
        slug.mkdir(parents=True, exist_ok=True)
        session_id = kwargs.pop("session_id", None) or str(uuid.uuid4())
        return TranscriptBuilder(slug / (session_id + ".jsonl"), cwd,
                                 session_id=session_id, **kwargs)

    return _make


@pytest.fixture
def now():
    """A fixed 'now' derived from the clock, never a hard-coded date."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
