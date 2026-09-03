"""Work out which project a Claude Code session belongs to.

Detection is purely derived from the session's working directory - there is no
configured project list anywhere in this system:

1. Ask git for the repository root (``git rev-parse --show-toplevel``) and use
   that directory's name.
2. If the directory is not a git repository (or git is unavailable), use the
   working directory's own name.

Results are cached on disk so the hook does not pay for a git subprocess on
every turn.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from .utils import read_json, write_json_atomic

# Characters Windows forbids in a path component, plus separators.
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_project_name(name: str) -> str:
    """Make a project name safe to use as a single directory component.

    Real-world project names are already valid directory names, so in practice
    this is a no-op; it exists to guarantee we never build an unwritable path.
    """
    cleaned = _INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return "unknown-project"
    if cleaned.upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:120]


def _basename(path: str) -> str:
    """Directory name of ``path``, handling trailing separators and drive roots."""
    if not path:
        return ""
    text = str(path).replace("\\", "/").rstrip("/")
    if not text:
        return ""
    # A bare drive root such as "D:" has no meaningful project name.
    if re.fullmatch(r"[A-Za-z]:", text):
        return text[0].upper() + "-drive"
    return text.rsplit("/", 1)[-1]


def git_root(cwd: str, timeout: float = 3.0) -> Optional[str]:
    """Return the git repository root for ``cwd``, or None.

    Never raises: a missing git binary, a non-repository directory, or a
    directory that no longer exists all simply yield None.
    """
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.decode("utf-8", "replace").strip()
    if not root:
        return None
    return str(Path(root))


class ProjectDetector:
    """Resolves a working directory to ``(project_name, git_root_or_None)``."""

    def __init__(self, config=None, cache_path: Optional[Path] = None):
        from .config import load_config  # local import keeps this module standalone

        self.config = config or load_config()
        self.use_git_root = bool(
            self.config.get("project_detection", "use_git_root", default=True)
        )
        self.ttl = float(
            self.config.get(
                "project_detection", "cache_ttl_seconds", default=86400
            )
        )
        self.git_timeout = float(
            self.config.get(
                "project_detection", "git_timeout_seconds", default=3.0
            )
        )
        self.cache_path = Path(
            cache_path or (self.config.state_dir / "project_cache.json")
        )
        self._cache: Dict[str, dict] = read_json(self.cache_path, default={}) or {}
        if not isinstance(self._cache, dict):
            self._cache = {}
        self._dirty = False

    # ---------------------------------------------------------------- api
    def is_ignored(self, cwd: str) -> bool:
        patterns = self.config.get("ignore_paths", default=[]) or []
        normalised = str(cwd or "").replace("\\", "/")
        for pattern in patterns:
            pat = str(pattern).replace("\\", "/")
            if normalised == pat or fnmatch.fnmatch(normalised, pat):
                return True
        return False

    def detect(self, cwd: str, git_branch_hint: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """Return ``(project_name, git_repository_root)`` for a working directory.

        ``git_branch_hint`` comes from the transcript. When it is absent we know
        Claude Code did not consider the directory a repository, which lets us
        skip the git subprocess entirely.
        """
        cwd = str(cwd or "").strip()
        if not cwd:
            return "unknown-project", None

        key = os.path.normcase(cwd.replace("\\", "/").rstrip("/"))
        entry = self._cache.get(key)
        if isinstance(entry, dict) and (time.time() - entry.get("at", 0)) < self.ttl:
            return entry.get("project") or "unknown-project", entry.get("git_root")

        root: Optional[str] = None
        if self.use_git_root and (git_branch_hint or git_branch_hint is None):
            root = git_root(cwd, timeout=self.git_timeout)

        name = _basename(root) if root else _basename(cwd)
        project = sanitize_project_name(name)

        self._cache[key] = {"project": project, "git_root": root, "at": time.time()}
        self._dirty = True
        return project, root

    def flush(self) -> None:
        """Persist the cache. Safe to call unconditionally."""
        if self._dirty:
            write_json_atomic(self.cache_path, self._cache)
            self._dirty = False
