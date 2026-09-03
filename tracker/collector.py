"""The collector: transcript turns in, JSONL interactions out.

This is what the Claude Code hook actually runs. The contract with Claude Code
is deliberately one-way and defensive:

* the hook reads a JSON payload on stdin and always exits 0;
* every failure is logged to ``logs/tracker.log`` and swallowed;
* the work is incremental, so a long session costs the same per turn as a short
  one.

Claude Code hook payloads do **not** carry token counts (verified against
2.1.252 - see README, "How token counts are obtained"). What they do carry is
``transcript_path``, and the transcript holds the API's own ``usage`` object for
every assistant message. So the hook is only a trigger: the numbers always come
from the transcript, and are never estimated.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import TRACKER_VERSION
from .config import Config, load_config
from .database import Database, open_database
from .parser import (
    TranscriptParser,
    iter_transcripts,
    primary_model,
    turn_has_content,
    turn_signature,
    turn_timestamp,
)
from .project_detector import ProjectDetector
from .storage import append_records
from .token_parser import finalize_usage
from .utils import (
    date_key,
    get_logger,
    iso_local,
    read_json,
    sha256_hex,
    short_hash,
    to_local,
    write_json_atomic,
)

#: Hook events the tracker subscribes to. ``Stop`` fires when a turn's response
#: is complete (so the usage exists); ``SessionEnd`` is the safety net that
#: catches anything the last ``Stop`` missed.
HOOK_EVENTS = ("Stop", "SessionEnd")


def interaction_id(turn: Dict[str, Any]) -> str:
    """Stable id for a turn, so re-processing can never double-count.

    ``(session_id, prompt_id)`` uniquely identifies a turn and is stable across
    reruns, restarts and full re-scans. Turns with no prompt id (assistant
    output in a resumed session) fall back to the first record's uuid.
    """
    session = turn.get("session_id") or "no-session"
    prompt_id = turn.get("prompt_id")
    if prompt_id:
        return short_hash(session, prompt_id)
    return short_hash(session, "orphan", turn.get("first_uuid"), turn.get("first_ts"))


def build_interaction(
    turn: Dict[str, Any],
    project: str,
    git_repository: Optional[str],
    config: Config,
    revision: int = 0,
) -> Optional[Dict[str, Any]]:
    """Render a parsed turn as the stored interaction record."""
    moment = turn_timestamp(turn)
    if moment is None:
        return None
    local = to_local(moment)

    prompt_text = turn.get("prompt")
    prompt_length = len(prompt_text) if isinstance(prompt_text, str) else None
    prompt_hash = (
        "sha256:" + sha256_hex(prompt_text) if isinstance(prompt_text, str) else None
    )

    stored_prompt: Optional[str] = None
    truncated = False
    if config.store_prompt_text and isinstance(prompt_text, str):
        limit = config.prompt_text_max_chars
        if limit and len(prompt_text) > limit:
            stored_prompt = prompt_text[:limit]
            truncated = True
        else:
            stored_prompt = prompt_text

    usage = finalize_usage(turn.get("usage") or {})
    models = sorted((turn.get("models") or {}).keys())

    return {
        "id": interaction_id(turn),
        "revision": revision,
        "timestamp": iso_local(moment),
        "date": date_key(moment),
        "year": local.year,
        "month": local.month,
        "day": local.day,
        "project": project,
        "working_directory": turn.get("cwd"),
        "git_repository": git_repository,
        "git_branch": turn.get("git_branch"),
        "session_id": turn.get("session_id"),
        "prompt_id": turn.get("prompt_id"),
        "model": primary_model(turn),
        "models": models,
        "prompt": stored_prompt,
        "prompt_length": prompt_length,
        "prompt_hash": prompt_hash,
        "prompt_truncated": truncated,
        "usage": usage,
        # Every number above came from the transcript's own usage object.
        "usage_source": "claude_code_transcript",
        "assistant_messages": turn.get("assistant_messages", 0),
        "sidechain_messages": turn.get("sidechain_messages", 0),
        "origin": turn.get("origin") or "unknown",
        "prompt_source": turn.get("prompt_source"),
        "claude_code_version": turn.get("version"),
        "entrypoint": turn.get("entrypoint"),
        "tracker_version": TRACKER_VERSION,
    }


class Collector:
    """Reads transcripts and records the turns it has not recorded before."""

    def __init__(self, config: Optional[Config] = None, db: Optional[Database] = None):
        self.config = config or load_config()
        self.log = get_logger("tracker", self.config.logs_dir)
        self.detector = ProjectDetector(self.config)
        self._db = db
        self._owns_db = db is None

    # ------------------------------------------------------------------
    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = open_database(self.config)
        return self._db

    def close(self) -> None:
        self.detector.flush()
        if self._owns_db and self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> "Collector":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------
    def _state_path(self, transcript: Path) -> Path:
        # Key on the full path so two sessions can never share state, and keep
        # the session id in the filename to stay debuggable.
        stem = Path(transcript).stem[:40] or "session"
        return (
            self.config.state_dir
            / "sessions"
            / ("%s-%s.json" % (stem, short_hash(str(transcript), length=8)))
        )

    # ------------------------------------------------------------------
    def sync_transcript(
        self,
        transcript: Path,
        finalize_prompt_ids: Optional[Sequence[str]] = None,
        finalize_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """Record any newly complete turns from one transcript.

        A turn is finalised when a later prompt has superseded it, when the
        ``Stop`` hook names it, or when the caller asks for everything
        (``SessionEnd``, backfill).
        """
        transcript = Path(transcript)
        if not transcript.is_file():
            return []

        state_path = self._state_path(transcript)
        state = read_json(state_path, default={}) or {}
        if not isinstance(state, dict):
            state = {}
        finalized: Dict[str, Any] = state.get("finalized") or {}

        parser = TranscriptParser(transcript, state)
        closed, new_state = parser.parse()

        candidates: List[Dict[str, Any]] = list(closed)
        open_turn = new_state.get("open_turn")
        wanted = set(finalize_prompt_ids or ())
        if open_turn is not None and (
            finalize_all or (open_turn.get("prompt_id") in wanted)
        ):
            candidates.append(open_turn)

        records: List[Dict[str, Any]] = []
        for turn in candidates:
            record = self._prepare(turn, finalized)
            if record is not None:
                records.append(record)

        if records:
            append_records(self.config.usage_dir, records)
            self.db.upsert(records)

        write_json_atomic(
            state_path,
            {
                "transcript_path": str(transcript),
                "offset": new_state.get("offset", 0),
                "open_turn": new_state.get("open_turn"),
                "finalized": finalized,
                "updated": time.time(),
            },
        )
        return records

    # ------------------------------------------------------------------
    def _prepare(
        self, turn: Dict[str, Any], finalized: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Build a record for a turn unless it is a duplicate of one already written."""
        if not turn_has_content(turn):
            return None
        if turn.get("origin") in ("meta",):
            return None
        if turn.get("origin") != "human" and not self.config.track_non_human_turns:
            return None

        cwd = turn.get("cwd") or ""
        if self.detector.is_ignored(cwd):
            return None

        key = turn.get("prompt_id") or ("uuid:" + str(turn.get("first_uuid")))
        signature = turn_signature(turn)
        previous = finalized.get(key)

        revision = 0
        if isinstance(previous, dict):
            if previous.get("sig") == signature:
                return None  # identical to what is already stored
            # The turn grew after we wrote it (a late-flushed assistant
            # message). Supersede the earlier line with a new revision.
            revision = int(previous.get("revision") or 0) + 1

        project, git_repository = self.detector.detect(cwd, turn.get("git_branch"))
        record = build_interaction(turn, project, git_repository, self.config, revision)
        if record is None:
            return None

        finalized[key] = {"sig": signature, "revision": revision, "id": record["id"]}
        return record

    # ------------------------------------------------------------------
    def handle_hook(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process one Claude Code hook payload."""
        event = payload.get("hook_event_name") or "unknown"
        transcript = payload.get("transcript_path")
        if not transcript:
            self.log.warning("hook %s carried no transcript_path", event)
            return []

        finalize_all = event in ("SessionEnd", "SubagentStop")
        prompt_ids = []
        if payload.get("prompt_id"):
            prompt_ids.append(payload["prompt_id"])

        records = self.sync_transcript(
            Path(transcript),
            finalize_prompt_ids=prompt_ids,
            finalize_all=finalize_all,
        )
        if records:
            self.log.info(
                "%s: recorded %d interaction(s) for project(s) %s",
                event,
                len(records),
                ", ".join(sorted({r["project"] for r in records})),
            )
        return records

    # ------------------------------------------------------------------
    def backfill(
        self,
        transcripts: Optional[Iterable[Path]] = None,
        budget_seconds: Optional[float] = None,
        progress=None,
    ) -> Dict[str, int]:
        """Import every historical transcript Claude Code has kept locally.

        Safe to re-run: already-recorded turns are skipped by their stable id.
        """
        started = time.time()
        paths = list(transcripts) if transcripts is not None else list(iter_transcripts())
        stats = {"transcripts": 0, "records": 0, "skipped": 0}

        for path in paths:
            if budget_seconds and (time.time() - started) > budget_seconds:
                stats["skipped"] = len(paths) - stats["transcripts"]
                break
            try:
                records = self.sync_transcript(path, finalize_all=True)
            except Exception as exc:  # never let one bad transcript stop the run
                self.log.warning("backfill failed for %s: %s", path, exc)
                continue
            stats["transcripts"] += 1
            stats["records"] += len(records)
            if progress:
                progress(path, len(records))
        return stats


# --------------------------------------------------------------------------
# Hook entry point
# --------------------------------------------------------------------------
def run_hook(stream=None) -> int:
    """Read a hook payload from stdin, record usage, and always succeed.

    Returning a non-zero exit code (or raising) from a hook is visible to the
    user inside Claude Code, so every path here is wrapped: the tracker's job is
    to be invisible when it works and harmless when it does not.
    """
    config = None
    log = None
    try:
        config = load_config()
        log = get_logger("tracker", config.logs_dir)

        raw = (stream or sys.stdin).read()
        if not raw or not raw.strip():
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0

        deadline = time.time() + config.hook_budget_seconds
        with Collector(config) as collector:
            collector.handle_hook(payload)
        if time.time() > deadline:
            log.warning("hook exceeded its %.1fs budget", config.hook_budget_seconds)
    except Exception as exc:
        try:
            if log is None:
                log = get_logger("tracker", (config.logs_dir if config else None))
            log.exception("hook failed, ignoring: %s", exc)
        except Exception:
            pass
    finally:
        # Hooks may read stdout as JSON; an empty object means "no opinion".
        try:
            sys.stdout.write("{}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via scripts/collect_usage.py
    raise SystemExit(run_hook())
