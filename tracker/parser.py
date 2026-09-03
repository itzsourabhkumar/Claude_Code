"""Turn Claude Code session transcripts into per-prompt "turns".

A Claude Code transcript (``~/.claude/projects/<slug>/<session-id>.jsonl``) is an
append-only log of records. The ones that matter here are:

* ``type: "user"``   - carries ``promptId``; either a real prompt (a ``text``
  content block) or a tool result belonging to the same prompt.
* ``type: "assistant"`` - carries ``message.usage`` with the real token counts,
  and no ``promptId`` of its own.

So a *turn* is one ``promptId``: the prompt itself plus every assistant message
produced before the next ``promptId`` appears. Because assistant records do not
name their prompt, attribution is positional, which is sound given the log is
strictly append-ordered.

The parser is incremental. It remembers a byte offset and the still-open turn,
so a hook firing on a long session re-reads only the bytes appended since last
time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .token_parser import add_usage, empty_usage, extract_usage, total_tokens
from .utils import parse_timestamp

#: Record types that can affect a turn. Everything else (attachments, AI
#: titles, file-history snapshots, queue operations...) is metadata we skip.
_INTERESTING = {"user", "assistant"}


def new_turn(prompt_id: Optional[str], record: Dict[str, Any]) -> Dict[str, Any]:
    """Start an accumulator for a new turn, seeded from ``record``."""
    return {
        "prompt_id": prompt_id,
        "session_id": record.get("sessionId"),
        "cwd": record.get("cwd"),
        "git_branch": record.get("gitBranch"),
        "version": record.get("version"),
        "entrypoint": record.get("entrypoint"),
        "first_ts": record.get("timestamp"),
        "last_ts": record.get("timestamp"),
        "first_uuid": record.get("uuid"),
        "prompt": None,
        "prompt_present": False,
        "origin": None,
        "prompt_source": None,
        "is_meta": False,
        "usage": empty_usage(),
        "assistant_messages": 0,
        "sidechain_messages": 0,
        "models": {},
        "message_ids": [],
    }


def extract_prompt_text(message: Any) -> Tuple[Optional[str], bool]:
    """Return ``(text, is_tool_result)`` for a user message.

    ``content`` is either a plain string or a list of blocks. Tool results are
    Claude Code feeding tool output back to the model - they are part of the
    turn, never the start of one.
    """
    if not isinstance(message, dict):
        return None, False
    content = message.get("content")

    if isinstance(content, str):
        return content, False

    if not isinstance(content, list):
        return None, False

    texts: List[str] = []
    is_tool_result = False
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_result":
            is_tool_result = True
        elif kind == "text":
            value = block.get("text")
            if isinstance(value, str):
                texts.append(value)
    if is_tool_result:
        return None, True
    return ("\n".join(texts) if texts else None), False


def classify_origin(record: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return ``(origin, prompt_source)`` for a user record.

    ``origin`` is one of ``human`` (the person typed or sent it),
    ``task-notification`` / ``system`` (Claude Code injected it), or ``meta``.
    Only ``human`` turns count as prompts in the dashboard's prompt totals; the
    rest are still recorded because they consume real tokens.
    """
    prompt_source = record.get("promptSource")
    if not isinstance(prompt_source, str):
        prompt_source = None

    if record.get("isMeta") is True:
        return "meta", prompt_source

    origin = record.get("origin")
    if isinstance(origin, dict):
        kind = origin.get("kind")
        if isinstance(kind, str) and kind:
            return kind, prompt_source

    if prompt_source in ("typed", "sdk", "suggestion_accepted"):
        return "human", prompt_source
    if prompt_source:
        return prompt_source, prompt_source
    return "unknown", prompt_source


def turn_signature(turn: Dict[str, Any]) -> str:
    """Fingerprint used to notice a turn that grew after it was first written.

    A ``Stop`` hook can fire a moment before the last assistant record is
    flushed to disk. When a later sync sees more messages or more tokens for the
    same turn, the signature changes and the collector emits a new revision.
    """
    return "%d:%d:%s" % (
        turn.get("assistant_messages", 0),
        turn.get("sidechain_messages", 0),
        total_tokens(turn.get("usage") or {}),
    )


def _record_prompt_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("promptId")
    return value if isinstance(value, str) and value else None


class TranscriptParser:
    """Incrementally parse one transcript file into turns."""

    def __init__(self, path: Path, state: Optional[Dict[str, Any]] = None):
        self.path = Path(path)
        state = state or {}
        self.offset: int = int(state.get("offset") or 0)
        self.open_turn: Optional[Dict[str, Any]] = state.get("open_turn")

    # ------------------------------------------------------------------
    def _read_new_lines(self) -> Tuple[List[bytes], int]:
        """Return complete lines appended since ``self.offset`` and the new offset.

        A partially written final line is left for the next run. If the file
        shrank (a forked or rewritten session), we restart from the beginning;
        duplicate suppression downstream makes that harmless.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            return [], self.offset

        if size < self.offset:
            self.offset = 0
            self.open_turn = None

        if size == self.offset:
            return [], self.offset

        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except OSError:
            return [], self.offset

        consumed = len(chunk)
        if not chunk.endswith(b"\n"):
            cut = chunk.rfind(b"\n")
            if cut == -1:
                return [], self.offset  # no complete line yet
            chunk = chunk[: cut + 1]
            consumed = len(chunk)

        return chunk.splitlines(), self.offset + consumed

    # ------------------------------------------------------------------
    def parse(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Parse newly appended records.

        Returns ``(closed_turns, state)``. ``closed_turns`` are turns a later
        prompt has superseded, so they are certainly complete. The still-open
        final turn lives in ``state['open_turn']``; the collector decides when
        to finalise it (the ``Stop`` hook names the prompt that just ended).
        """
        import json

        lines, new_offset = self._read_new_lines()
        closed: List[Dict[str, Any]] = []

        for raw in lines:
            try:
                record = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") not in _INTERESTING:
                continue

            is_sidechain = bool(record.get("isSidechain"))
            prompt_id = _record_prompt_id(record)

            # A new promptId opens a new turn. Sidechain (subagent) records ride
            # along with the parent turn instead of starting one.
            if prompt_id and not is_sidechain:
                current = self.open_turn
                if current is None or current.get("prompt_id") != prompt_id:
                    if current is not None:
                        closed.append(current)
                    self.open_turn = new_turn(prompt_id, record)

            if self.open_turn is None:
                # Assistant output before any prompt in the parsed window, e.g. a
                # resumed session. Keep the tokens under an unattributed turn.
                self.open_turn = new_turn(None, record)
                self.open_turn["origin"] = "unattributed"

            turn = self.open_turn
            if record.get("timestamp"):
                turn["last_ts"] = record.get("timestamp")
                if not turn.get("first_ts"):
                    turn["first_ts"] = record.get("timestamp")
            for key, source in (
                ("cwd", "cwd"),
                ("git_branch", "gitBranch"),
                ("version", "version"),
                ("entrypoint", "entrypoint"),
                ("session_id", "sessionId"),
            ):
                if not turn.get(key) and record.get(source):
                    turn[key] = record.get(source)

            if record.get("type") == "user":
                self._absorb_user(turn, record, is_sidechain)
            else:
                self._absorb_assistant(turn, record, is_sidechain)

        self.offset = new_offset
        return closed, self.state()

    # ------------------------------------------------------------------
    @staticmethod
    def _absorb_user(turn: Dict[str, Any], record: Dict[str, Any], is_sidechain: bool) -> None:
        if is_sidechain:
            return
        text, is_tool_result = extract_prompt_text(record.get("message"))
        if is_tool_result:
            return
        if not turn.get("prompt_present"):
            origin, prompt_source = classify_origin(record)
            turn["origin"] = origin
            turn["prompt_source"] = prompt_source
            turn["is_meta"] = record.get("isMeta") is True
            if text is not None:
                turn["prompt"] = text
                turn["prompt_present"] = True

    @staticmethod
    def _absorb_assistant(turn: Dict[str, Any], record: Dict[str, Any], is_sidechain: bool) -> None:
        message = record.get("message")
        if not isinstance(message, dict):
            return

        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            # Retries and streaming can repeat a message id; count it once.
            if message_id in turn["message_ids"]:
                return
            turn["message_ids"].append(message_id)

        usage = extract_usage(message)
        if usage:
            add_usage(turn["usage"], usage)
            output = usage.get("output_tokens") or 0
        else:
            output = 0

        model = message.get("model")
        if isinstance(model, str) and model:
            turn["models"][model] = turn["models"].get(model, 0) + output

        if is_sidechain:
            turn["sidechain_messages"] = turn.get("sidechain_messages", 0) + 1
        else:
            turn["assistant_messages"] = turn.get("assistant_messages", 0) + 1

    # ------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        return {"offset": self.offset, "open_turn": self.open_turn}


def parse_full(path: Path) -> List[Dict[str, Any]]:
    """Parse an entire transcript and return every turn, including the last."""
    parser = TranscriptParser(path)
    closed, state = parser.parse()
    turns = list(closed)
    if state.get("open_turn"):
        turns.append(state["open_turn"])
    return turns


def turn_has_content(turn: Dict[str, Any]) -> bool:
    """True when a turn is worth recording at all.

    A turn with neither token usage nor a prompt is a bookkeeping artefact (for
    example an aborted prompt) and is dropped.
    """
    if total_tokens(turn.get("usage") or {}) is not None:
        return True
    return bool(turn.get("prompt_present"))


def primary_model(turn: Dict[str, Any]) -> Optional[str]:
    """The model that produced most of the turn's output tokens."""
    models: Dict[str, int] = turn.get("models") or {}
    if not models:
        return None
    return max(models.items(), key=lambda item: (item[1], item[0]))[0]


def turn_timestamp(turn: Dict[str, Any]):
    """Preferred timestamp for a turn: when the prompt was submitted."""
    return parse_timestamp(turn.get("first_ts")) or parse_timestamp(turn.get("last_ts"))


def iter_transcripts(projects_dir: Optional[Path] = None) -> Iterable[Path]:
    """Yield every Claude Code transcript on this machine.

    Claude Code keeps one directory per working directory under
    ``~/.claude/projects`` (relocatable with ``CLAUDE_CONFIG_DIR``, on every
    platform); the directory name is a slug, which is why the tracker reads
    ``cwd`` out of the records themselves instead of the slug.
    """
    from .platform_utils import claude_projects_dir

    base = Path(projects_dir) if projects_dir else claude_projects_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob("*/*.jsonl"))
