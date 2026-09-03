"""Small shared helpers: logging, time, hashing, locking, JSON I/O.

Everything here is deliberately dependency-free (standard library only) so the
hook can run under any Python 3.9+ interpreter without a virtualenv.
"""

from __future__ import annotations

import datetime as _dt
import errno
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Optional

_LOGGERS: dict = {}


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def get_logger(name: str, logs_dir: Optional[Path] = None) -> logging.Logger:
    """Return a logger writing to ``logs/<name>.log`` that never raises.

    Tracker logging must never be able to take Claude Code down, so failure to
    open the log file degrades to a null handler rather than propagating.
    """
    key = f"{name}:{logs_dir}"
    if key in _LOGGERS:
        return _LOGGERS[key]

    # The logs directory is part of the logger's identity: two roots in one
    # process must not share a handler, or the second one's records would be
    # written to the first one's file.
    suffix = hashlib.sha256(str(logs_dir).encode("utf-8", "replace")).hexdigest()[:8]
    logger = logging.getLogger(f"cctracker.{name}.{suffix}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler: logging.Handler
        try:
            if logs_dir is not None:
                logs_dir = Path(logs_dir)
                logs_dir.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(
                    logs_dir / (name + ".log"), encoding="utf-8", delay=True
                )
            else:
                handler = logging.NullHandler()
        except Exception:
            handler = logging.NullHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    _LOGGERS[key] = logger
    return logger


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------
#: Timezone used for calendar bucketing, set once by ``config.load_config``.
_TIMEZONE_SPEC = "local"
_TIMEZONE_RESOLVED: Optional[_dt.tzinfo] = None

_OFFSET_PATTERN = re.compile(r"^(?P<sign>[+-])(?P<h>\d{1,2}):?(?P<m>\d{2})?$")


def set_timezone(spec: Optional[str]) -> None:
    """Choose the timezone days are bucketed in.

    ``"local"`` (the default) follows the machine. Anything else is resolved
    once and cached; see :func:`resolve_timezone` for the accepted forms.
    """
    global _TIMEZONE_SPEC, _TIMEZONE_RESOLVED
    normalised = (spec or "local").strip() or "local"
    if normalised != _TIMEZONE_SPEC:
        _TIMEZONE_SPEC = normalised
        _TIMEZONE_RESOLVED = None


def resolve_timezone(spec: Optional[str]) -> Optional[_dt.tzinfo]:
    """Turn a config timezone string into a tzinfo, or None to follow the machine.

    Three forms are accepted, in this order:

    * ``local`` / empty - follow the machine's own timezone;
    * a fixed offset such as ``+05:30``, ``-08:00`` or ``UTC`` - works on every
      platform with no extra packages;
    * an IANA name such as ``Asia/Kolkata`` - needs a timezone database, which
      Linux and macOS ship but Windows does not (``pip install tzdata`` adds it).

    An unresolvable value falls back to the machine's timezone rather than
    raising, because this runs inside a Claude Code hook.
    """
    text = (spec or "local").strip()
    if not text or text.lower() == "local":
        return None
    if text.upper() in ("UTC", "Z", "GMT"):
        return _dt.timezone.utc

    match = _OFFSET_PATTERN.match(text)
    if match:
        hours = int(match.group("h"))
        minutes = int(match.group("m") or 0)
        if hours <= 23 and minutes <= 59:
            delta = _dt.timedelta(hours=hours, minutes=minutes)
            return _dt.timezone(-delta if match.group("sign") == "-" else delta)

    try:
        from zoneinfo import ZoneInfo  # stdlib on 3.9+

        return ZoneInfo(text)
    except Exception:
        return None


def local_timezone() -> _dt.tzinfo:
    """The timezone calendar days are bucketed in.

    Defaults to the machine's own timezone, which is what makes a day in the
    dashboard match the user's actual working day.
    """
    global _TIMEZONE_RESOLVED
    if _TIMEZONE_RESOLVED is None:
        _TIMEZONE_RESOLVED = resolve_timezone(_TIMEZONE_SPEC)
    if _TIMEZONE_RESOLVED is not None:
        return _TIMEZONE_RESOLVED
    return _dt.datetime.now().astimezone().tzinfo or _dt.timezone.utc


def parse_timestamp(value: Any) -> Optional[_dt.datetime]:
    """Parse an ISO-8601 timestamp (Claude Code writes UTC with a Z suffix).

    Returns a timezone-aware datetime, or None when unparseable.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def to_local(value: _dt.datetime) -> _dt.datetime:
    """Convert an aware datetime into local time.

    Calendar bucketing (year/month/day directories) uses local time so a day in
    the dashboard matches the user's actual working day.
    """
    return value.astimezone(local_timezone())


def iso_local(value: _dt.datetime) -> str:
    """ISO-8601 string in local time, e.g. 2026-09-03T14:32:10+05:30."""
    return to_local(value).isoformat(timespec="seconds")


def now_local() -> _dt.datetime:
    return _dt.datetime.now(local_timezone())


def date_key(value: _dt.datetime) -> str:
    return to_local(value).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------
def sha256_hex(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def short_hash(*parts: Any, length: int = 32) -> str:
    return sha256_hex(*parts)[:length]


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------
def read_json(path: Path, default: Any = None) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json_atomic(path: Path, payload: Any) -> bool:
    """Write JSON via temp file + replace so readers never see a partial file."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp" + str(os.getpid()))
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield objects from a JSONL file, skipping unparseable/partial lines."""
    try:
        handle = Path(path).open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


# --------------------------------------------------------------------------
# Cross-platform advisory file lock
# --------------------------------------------------------------------------
class FileLock:
    """Best-effort inter-process lock built on O_CREAT | O_EXCL.

    Serialises appends when several Claude Code sessions finish a turn at the
    same moment. Acquisition failure is not fatal: appending one short line is
    atomic enough in practice, so we proceed rather than lose the record.
    """

    def __init__(self, path: Path, timeout: float = 5.0, stale_after: float = 60.0):
        self.path = Path(path)
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self) -> "FileLock":
        deadline = time.time() + self.timeout
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.acquired = True
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    return self
                # Reclaim a lock left behind by a killed process.
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_after:
                        _unlink_quiet(self.path)
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    return self
                time.sleep(0.02)

    def __exit__(self, *_exc) -> bool:
        if self.acquired:
            _unlink_quiet(self.path)
        return False


def _unlink_quiet(path: Path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass


# --------------------------------------------------------------------------
# Formatting (CLI / reports)
# --------------------------------------------------------------------------
def human_tokens(value: Optional[int]) -> str:
    """Compact token count: 1234567 -> '1.2M'."""
    if value is None:
        return "-"
    value = int(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            return "%.1f%s" % (value / limit, suffix)
    return str(value)


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)
