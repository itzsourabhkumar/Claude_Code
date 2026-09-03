"""Everything that has to know which operating system it is running on.

The rest of the tracker is written to be OS-agnostic: it uses :mod:`pathlib`,
never builds a path by string concatenation, and never shells out to a named
shell. The handful of decisions that genuinely differ between Windows, Linux and
macOS are collected here so there is exactly one place to look - and one place to
test:

* where Claude Code keeps its configuration and transcripts;
* which interpreter the hook command should be pinned to;
* how a path is quoted inside the hook command string;
* which convenience launcher to name in help text;
* whether a configured dashboard host is safely loopback-only.

Standard library only, so this module can be imported by the hook under any
interpreter without a virtualenv.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Dict, List, Optional

WINDOWS = "windows"
LINUX = "linux"
MACOS = "macos"
UNKNOWN = "unknown"

#: Human-readable label per platform key, used in help text and status output.
OS_LABELS = {
    WINDOWS: "Windows",
    LINUX: "Linux",
    MACOS: "macOS",
    UNKNOWN: "unknown OS",
}


# --------------------------------------------------------------------------
# Which OS is this?
# --------------------------------------------------------------------------
def current_os() -> str:
    """One of ``windows`` / ``linux`` / ``macos`` / ``unknown``.

    Derived from :data:`sys.platform` rather than ``platform.system()`` because
    it needs no subprocess and cannot be slowed down by a stale uname cache.
    """
    prefix = sys.platform
    if prefix.startswith("win") or prefix == "cygwin":
        return WINDOWS
    if prefix == "darwin":
        return MACOS
    if prefix.startswith("linux"):
        return LINUX
    # FreeBSD, Solaris and friends behave like Linux for everything we do.
    if os.name == "posix":
        return LINUX
    return UNKNOWN


def is_windows() -> bool:
    return current_os() == WINDOWS


def is_macos() -> bool:
    return current_os() == MACOS


def is_linux() -> bool:
    return current_os() == LINUX


def is_posix() -> bool:
    return not is_windows()


def os_label(key: Optional[str] = None) -> str:
    return OS_LABELS.get(key or current_os(), OS_LABELS[UNKNOWN])


# --------------------------------------------------------------------------
# User and Claude Code locations
# --------------------------------------------------------------------------
def home_dir() -> Path:
    """The user's home directory on any platform.

    ``Path.home()`` raises when neither ``HOME`` nor the Windows profile
    variables are set (bare containers, some service accounts), so fall back to
    ``expanduser`` and finally to the working directory rather than blowing up
    inside a Claude Code hook.
    """
    try:
        return Path.home()
    except (RuntimeError, OSError):
        pass
    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        return Path(expanded)
    return Path.cwd()


def claude_config_dir() -> Path:
    """Claude Code's configuration directory.

    Claude Code uses ``~/.claude`` on Windows, Linux and macOS alike, and lets
    the user move it with ``CLAUDE_CONFIG_DIR``. That environment variable is the
    supported override on every platform, which is why nothing here branches on
    the OS: the *mechanism* is the same, only the expansion of ``~`` differs.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override and override.strip():
        return Path(os.path.expanduser(override.strip()))
    return home_dir() / ".claude"


def claude_settings_path() -> Path:
    """The user-level ``settings.json`` the hook is registered in."""
    return claude_config_dir() / "settings.json"


def claude_projects_dir() -> Path:
    """Where Claude Code keeps session transcripts (one directory per cwd)."""
    return claude_config_dir() / "projects"


def find_claude_executable() -> Optional[str]:
    """Locate the ``claude`` CLI, or None when it is not installed.

    Used only for diagnostics - the tracker never runs Claude Code.
    """
    for name in ("claude", "claude.cmd", "claude.exe") if is_windows() else ("claude",):
        found = shutil.which(name)
        if found:
            return found
    # npm global installs are not always on a non-interactive shell's PATH.
    candidates: List[Path] = [claude_config_dir() / "local" / "claude"]
    if is_windows():
        candidates.append(claude_config_dir() / "local" / "claude.cmd")
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "claude.cmd")
    else:
        candidates.extend([
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
            home_dir() / ".local" / "bin" / "claude",
        ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def find_git_executable() -> Optional[str]:
    """Locate ``git``. Project detection degrades gracefully without it."""
    return shutil.which("git.exe") if is_windows() else shutil.which("git")


# --------------------------------------------------------------------------
# Python interpreter / virtualenv
# --------------------------------------------------------------------------
def venv_bin_dirname() -> str:
    """``Scripts`` on Windows, ``bin`` everywhere else."""
    return "Scripts" if is_windows() else "bin"


def venv_python_names() -> tuple:
    return ("python.exe", "pythonw.exe") if is_windows() else ("python3", "python")


def venv_python(root: Path) -> Optional[Path]:
    """The interpreter inside ``<root>/.venv`` or ``<root>/venv``, if present.

    Both layouts are probed on every platform - a data directory copied from
    Windows to Linux (or the reverse) must not make the tracker pick an
    interpreter that cannot run - so candidacy is decided by the file existing,
    never by the OS alone.
    """
    root = Path(root)
    for venv_name in (".venv", "venv"):
        for bin_dir in (venv_bin_dirname(), "bin", "Scripts"):
            for exe in venv_python_names() + ("python3", "python", "python.exe"):
                candidate = root / venv_name / bin_dir / exe
                if candidate.is_file():
                    return candidate
    return None


def python_executable(root: Path) -> str:
    """Absolute interpreter path to bake into the Claude Code hook command.

    The hook has to keep working long after the shell that installed it is gone,
    so it can never rely on ``PATH`` or on an activated virtualenv.
    """
    found = venv_python(root)
    if found is not None:
        return str(found)
    return sys.executable or ("python" if is_windows() else "python3")


def python_command_name() -> str:
    """What the README should tell this platform's user to type."""
    return "python" if is_windows() else "python3"


def activate_commands() -> List[str]:
    """Shell lines that activate ``.venv`` on this platform."""
    if is_windows():
        return [r".venv\Scripts\Activate.ps1", r".venv\Scripts\activate.bat"]
    return ["source .venv/bin/activate"]


# --------------------------------------------------------------------------
# Hook command construction
# --------------------------------------------------------------------------
def normalise_hook_path(path) -> str:
    """Render a path for embedding in a hook command string.

    On Windows the separator is normalised to ``/``: both ``cmd.exe`` and the
    JSON settings file handle it, and it avoids a wall of escaped backslashes in
    ``settings.json``. On POSIX a backslash is a legal filename character, so the
    path is left exactly as it is.
    """
    text = str(path)
    return text.replace("\\", "/") if is_windows() else text


def quote_hook_arg(value: str) -> str:
    """Quote one argument of the hook command for the platform's shell."""
    text = str(value)
    if is_windows():
        # cmd.exe has no escape for a quote inside a quoted string; paths
        # containing one cannot occur on Windows, so quoting is enough.
        return '"%s"' % text
    if text and all(ch.isalnum() or ch in "._-+=:/@" for ch in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


# --------------------------------------------------------------------------
# Dashboard host safety
# --------------------------------------------------------------------------
def is_loopback_host(host: str) -> bool:
    """True only for addresses that cannot be reached from another machine.

    The dashboard serves prompt text, so the bind address is validated rather
    than trusted: a config file that says ``0.0.0.0`` must not silently publish
    it to the network.
    """
    text = str(host or "").strip()
    if not text:
        return False
    if text.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def port_is_free(host: str, port: int) -> bool:
    """Best-effort check used to give a useful message before binding."""
    family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if not is_windows():
            # Without this a socket in TIME_WAIT reads as "in use" on POSIX.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((str(host), int(port)))
        except OSError:
            return False
    return True


# --------------------------------------------------------------------------
# Convenience launchers (help text only - all logic lives in Python)
# --------------------------------------------------------------------------
def dashboard_launcher() -> str:
    """The convenience script to suggest for starting the dashboard."""
    if is_windows():
        return r".\start_dashboard.bat"
    return "./start_dashboard.sh"


def install_command() -> str:
    return "%s scripts/install_hooks.py" % python_command_name()


def uninstall_command() -> str:
    return "%s scripts/uninstall_hooks.py" % python_command_name()


def server_command() -> str:
    return "%s server.py" % python_command_name()


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------
def describe_platform(root: Optional[Path] = None) -> Dict[str, Optional[str]]:
    """A flat, printable picture of the environment, for ``cli status``."""
    return {
        "os": os_label(),
        "platform": sys.platform,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "python_executable": sys.executable,
        "hook_interpreter": python_executable(root) if root else None,
        "claude_config_dir": str(claude_config_dir()),
        "claude_settings": str(claude_settings_path()),
        "claude_transcripts": str(claude_projects_dir()),
        "claude_executable": find_claude_executable() or "not found on PATH",
        "git_executable": find_git_executable() or "not found on PATH",
    }
