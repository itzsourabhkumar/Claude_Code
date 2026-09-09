"""Configuration loading.

``config.json`` at the repository root holds every tunable. Anything missing
falls back to the defaults below, so a truncated or hand-edited config can never
stop the hook from recording usage.

Every path in the file may be written either relative to the repository root
(the portable default, and what ships in ``config.json``) or as an absolute path
for a machine that keeps its data elsewhere. Relative paths use ``/`` separators
in the JSON and are resolved through :class:`pathlib.Path`, so the same
``config.json`` works unchanged on Windows, Linux and macOS.

Environment overrides, useful for CI and for running two installations side by
side (see ``.env.example``):

``CCTRACKER_ROOT``
    Repository root, which is also what relative paths resolve against.
``CCTRACKER_CONFIG``
    An explicit path to a config file, instead of ``<root>/config.json``.
``CCTRACKER_DATA_DIR``, ``CCTRACKER_LOGS_DIR``
    Override the data and log directories without editing the file.
``CCTRACKER_PORT``, ``CCTRACKER_HOST``
    Override the dashboard bind address.
``CCTRACKER_TIMEZONE``
    Timezone used to bucket interactions into calendar days.
``CCTRACKER_USD_TO_INR``
    Exchange rate used for the dashboard's rupee figures.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .platform_utils import is_loopback_host
from .pricing import DEFAULT_USD_TO_INR
from .utils import read_json, set_timezone

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
    # Timezone used to bucket interactions into YYYY/MM/DD directories and into
    # dashboard days. "local" follows the machine; an IANA name such as
    # "Asia/Kolkata" or a fixed offset such as "+05:30" pins it instead.
    "timezone": "local",
    "paths": {
        # Base directory for everything the tracker generates. The three keys
        # below default underneath it and may be repointed individually.
        "data_dir": "data",
        "usage_dir": "data/usage",
        "state_dir": "data/state",
        "index_path": "data/index.sqlite3",
        "reports_dir": "reports",
        "logs_dir": "logs",
    },
    "server": {
        # Must stay a loopback address: the dashboard serves prompt text and has
        # no authentication. A non-loopback value is refused at startup.
        "host": "127.0.0.1",
        "port": 8765,
        # Open a browser window when the dashboard starts.
        "open_browser": True,
    },
    "dashboard": {
        # Seconds between automatic refreshes; 0 leaves auto-refresh off, which
        # is the default. The dashboard's own selector overrides this per visit.
        "auto_refresh_seconds": 0,
        # Rows per page in the prompt history table.
        "page_size": 25,
    },
    "pricing": {
        # Show cost alongside token counts. Costs are always derived at read
        # time from the stored token counts, so turning this off (or changing a
        # rate below) never alters recorded data.
        "enabled": True,
        # USD -> INR. Static by design: this tracker makes no network calls, and
        # a rate that moved on its own would make the same data report a
        # different figure every day. Set it to the rate you account at.
        "usd_to_inr": 88.0,
        # Which prompt-cache TTL to assume for cache-creation tokens. Claude
        # Code records how many tokens were written to the cache but not the TTL
        # they were written with, so this is an assumption, not a measurement.
        # "5m" (1.25x input) is Claude Code's default; "1h" is 2x input.
        "cache_write_ttl": "5m",
        # Per-model USD-per-million overrides, merged over the built-in table:
        #   {"my-model": {"input": 5.0, "output": 25.0}}
        # Cache prices default to Anthropic's multipliers when omitted.
        "model_prices": {},
    },
    "project_detection": {
        # Prefer the git repository root's directory name.
        "use_git_root": True,
        # Seconds to cache a working-directory -> project resolution.
        "cache_ttl_seconds": 86400,
        # Seconds to wait for `git rev-parse` before falling back.
        "git_timeout_seconds": 3.0,
    },
    # Working directories to skip entirely (exact match or glob). Written with
    # "/" separators; Windows paths are matched with "/" too.
    "ignore_paths": [],
    # Hook safety: give up a sync after this long rather than delay Claude Code.
    "hook_budget_seconds": 8.0,
}

#: Auto-refresh intervals the dashboard offers, in seconds (0 = off).
AUTO_REFRESH_CHOICES = (0, 30, 60, 300)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_overrides() -> Dict[str, Any]:
    """Config fragments taken from the environment, applied over the file."""
    override: Dict[str, Any] = {}
    paths: Dict[str, Any] = {}
    server: Dict[str, Any] = {}

    data_dir = os.environ.get("CCTRACKER_DATA_DIR")
    if data_dir:
        base = data_dir.rstrip("/\\")
        paths.update({
            "data_dir": base,
            "usage_dir": base + "/usage",
            "state_dir": base + "/state",
            "index_path": base + "/index.sqlite3",
        })
    logs_dir = os.environ.get("CCTRACKER_LOGS_DIR")
    if logs_dir:
        paths["logs_dir"] = logs_dir

    host = os.environ.get("CCTRACKER_HOST")
    if host:
        server["host"] = host
    port = os.environ.get("CCTRACKER_PORT")
    if port:
        try:
            server["port"] = int(port)
        except ValueError:
            pass

    timezone = os.environ.get("CCTRACKER_TIMEZONE")
    if timezone:
        override["timezone"] = timezone

    rate = os.environ.get("CCTRACKER_USD_TO_INR")
    if rate:
        try:
            override["pricing"] = {"usd_to_inr": float(rate)}
        except ValueError:
            pass

    if paths:
        override["paths"] = paths
    if server:
        override["server"] = server
    return override


class InvalidConfig(ValueError):
    """Raised for a setting that would be unsafe to honour."""


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
        # Path() understands "/" on every platform, so a config written on one
        # OS resolves correctly on the others.
        candidate = Path(str(raw).replace("\\", "/"))
        return candidate if candidate.is_absolute() else (self.root / candidate)

    # -- well-known paths --------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self._path("data_dir")

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
    def timezone(self) -> str:
        return str(self.get("timezone", default="local") or "local")

    @property
    def host(self) -> str:
        """The dashboard bind address, refused unless it is loopback-only."""
        value = str(self.get("server", "host", default="127.0.0.1") or "127.0.0.1")
        if not is_loopback_host(value):
            raise InvalidConfig(
                "server.host is %r, which is reachable from other machines. "
                "The dashboard serves your prompt text and has no authentication, "
                "so it may only bind to a loopback address such as 127.0.0.1." % value
            )
        return value

    @property
    def port(self) -> int:
        try:
            port = int(self.get("server", "port", default=8765))
        except (TypeError, ValueError):
            return 8765
        return port if 0 <= port <= 65535 else 8765

    @property
    def open_browser(self) -> bool:
        return bool(self.get("server", "open_browser", default=True))

    @property
    def auto_refresh_seconds(self) -> int:
        try:
            value = int(self.get("dashboard", "auto_refresh_seconds", default=0))
        except (TypeError, ValueError):
            return 0
        return value if value in AUTO_REFRESH_CHOICES else 0

    @property
    def page_size(self) -> int:
        try:
            value = int(self.get("dashboard", "page_size", default=25))
        except (TypeError, ValueError):
            return 25
        return value if value in (25, 50, 100, 250) else 25

    # -- pricing -----------------------------------------------------------
    @property
    def pricing_enabled(self) -> bool:
        return bool(self.get("pricing", "enabled", default=True))

    @property
    def usd_to_inr(self) -> float:
        """Exchange rate used for every rupee figure. Always positive."""
        try:
            rate = float(self.get("pricing", "usd_to_inr", default=DEFAULT_USD_TO_INR))
        except (TypeError, ValueError):
            return DEFAULT_USD_TO_INR
        return rate if rate > 0 else DEFAULT_USD_TO_INR

    @property
    def cache_write_ttl(self) -> str:
        value = str(self.get("pricing", "cache_write_ttl", default="5m") or "5m").lower()
        return "1h" if value in ("1h", "60m", "hour") else "5m"

    @property
    def model_prices(self) -> Dict[str, Any]:
        """User-supplied per-model price overrides, merged over the built-ins."""
        value = self.get("pricing", "model_prices", default={})
        return value if isinstance(value, dict) else {}

    @property
    def hook_budget_seconds(self) -> float:
        try:
            return float(self.get("hook_budget_seconds", default=8.0))
        except (TypeError, ValueError):
            return 8.0

    # -- creation ----------------------------------------------------------
    def ensure_directories(self) -> None:
        """Create the directories the tracker writes to.

        Failures are swallowed: a read-only data directory must degrade to "no
        data recorded", never to a hook that breaks Claude Code.
        """
        for path in (self.usage_dir, self.state_dir, self.logs_dir,
                     self.reports_dir, self.index_path.parent):
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError:
                continue


def config_path_for(root: Path) -> Path:
    """The config file for a root, honouring ``CCTRACKER_CONFIG``."""
    override = os.environ.get("CCTRACKER_CONFIG")
    if override and override.strip():
        return Path(os.path.expanduser(override.strip()))
    return Path(root) / "config.json"


def load_config(root: Optional[Path] = None, path: Optional[Path] = None) -> Config:
    """Load ``config.json``, merged over the defaults and the environment.

    ``CCTRACKER_ROOT`` overrides the repository root, which lets the test suite
    and the hook point at an isolated data directory.
    """
    if root is None:
        root = Path(os.environ.get("CCTRACKER_ROOT") or ROOT)
    root = Path(root).expanduser().resolve()
    if path is None:
        path = config_path_for(root)
    user_data = read_json(path, default={}) or {}
    if not isinstance(user_data, dict):
        user_data = {}

    merged = _deep_merge(_deep_merge(DEFAULTS, user_data), _env_overrides())
    config = Config(merged, root)
    # Calendar bucketing happens deep inside storage and reporting; setting the
    # process-wide timezone here keeps that code free of config plumbing.
    set_timezone(config.timezone)
    return config
