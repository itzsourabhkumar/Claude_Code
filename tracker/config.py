"""Configuration loading.

``config.json`` at the repository root holds every tunable. Anything missing
falls back to the defaults below, so a truncated or hand-edited config can never
stop the hook from recording usage.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

from .utils import read_json

# The package lives at <root>/tracker/config.py
ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    # Store the full prompt text. Set to false to keep only length + hash.
    "store_prompt_text": True,
    # Prompts longer than this are truncated on disk (0 disables truncation).
    "prompt_text_max_chars": 8000,
    # Record turns that Claude Code initiated itself (task notifications,
    # system reminders). They cost real tokens, so they are tracked but flagged
    # via the "origin" field and excluded from the human prompt count.
    "track_non_human_turns": True,
    "paths": {
        "usage_dir": "data/usage",
        "state_dir": "data/state",
        "index_path": "data/index.sqlite3",
        "reports_dir": "reports",
        "logs_dir": "logs",
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "project_detection": {
        # Prefer the git repository root's directory name.
        "use_git_root": True,
        # Seconds to cache a working-directory -> project resolution.
        "cache_ttl_seconds": 86400,
        # Seconds to wait for `git rev-parse` before falling back.
        "git_timeout_seconds": 3.0,
    },
    # Working directories to skip entirely (exact match or glob).
    "ignore_paths": [],
    # Hook safety: give up a sync after this long rather than delay Claude Code.
    "hook_budget_seconds": 8.0,
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Config:
    """Resolved configuration with absolute paths."""

    def __init__(self, data: Dict[str, Any], root: Path):
        self.root = Path(root)
        self.data = data

    # -- generic access ----------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _path(self, key: str) -> Path:
        raw = self.get("paths", key, default=DEFAULTS["paths"][key])
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else (self.root / candidate)

    # -- well-known paths --------------------------------------------------
    @property
    def usage_dir(self) -> Path:
        return self._path("usage_dir")

    @property
    def state_dir(self) -> Path:
        return self._path("state_dir")

    @property
    def index_path(self) -> Path:
        return self._path("index_path")

    @property
    def reports_dir(self) -> Path:
        return self._path("reports_dir")

    @property
    def logs_dir(self) -> Path:
        return self._path("logs_dir")

    # -- well-known flags --------------------------------------------------
    @property
    def store_prompt_text(self) -> bool:
        return bool(self.get("store_prompt_text", default=True))

    @property
    def prompt_text_max_chars(self) -> int:
        try:
            return int(self.get("prompt_text_max_chars", default=8000))
        except (TypeError, ValueError):
            return 8000

    @property
    def track_non_human_turns(self) -> bool:
        return bool(self.get("track_non_human_turns", default=True))

    @property
    def host(self) -> str:
        # Hard-pinned to loopback below in server.py; this is informational.
        return str(self.get("server", "host", default="127.0.0.1"))

    @property
    def port(self) -> int:
        try:
            return int(self.get("server", "port", default=8765))
        except (TypeError, ValueError):
            return 8765

    @property
    def hook_budget_seconds(self) -> float:
        try:
            return float(self.get("hook_budget_seconds", default=8.0))
        except (TypeError, ValueError):
            return 8.0


def load_config(root: Path | None = None, path: Path | None = None) -> Config:
    """Load ``config.json``, merged over the defaults.

    ``CCTRACKER_ROOT`` overrides the repository root, which lets the test suite
    and the hook point at an isolated data directory.
    """
    if root is None:
        root = Path(os.environ.get("CCTRACKER_ROOT") or ROOT)
    root = Path(root).resolve()
    if path is None:
        path = root / "config.json"
    user_data = read_json(path, default={}) or {}
    if not isinstance(user_data, dict):
        user_data = {}
    return Config(_deep_merge(DEFAULTS, user_data), root)
