"""Install and remove the Claude Code hooks that drive the tracker.

All of the settings-file surgery lives here rather than in PowerShell so that it
is testable and so install/uninstall can never disagree about what belongs to
this project.

Rules this module holds itself to:

* only ever add or remove hook entries carrying our marker
  (``--cctracker``); every other hook and every unrelated setting is copied
  through untouched;
* take a timestamped backup of ``settings.json`` before writing;
* be idempotent - installing twice leaves exactly one hook per event.

Usage::

    python -m tracker.hooks install
    python -m tracker.hooks uninstall
    python -m tracker.hooks status
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collector import HOOK_EVENTS
from .config import ROOT
from . import platform_utils
from .utils import now_local, read_json, write_json_atomic

#: Marker that identifies a hook entry as belonging to this tracker.
MARKER = "--cctracker"

#: Seconds Claude Code will wait for our hook before abandoning it. The collector
#: normally finishes in well under a second; this is only a backstop.
HOOK_TIMEOUT = 20


def claude_settings_path() -> Path:
    """The user-level ``settings.json``.

    ``~/.claude/settings.json`` on Windows, Linux and macOS alike, relocatable
    with ``CLAUDE_CONFIG_DIR`` on all three - see ``tracker.platform_utils``,
    which owns every OS-dependent location in this project.
    """
    return platform_utils.claude_settings_path()


def python_executable() -> str:
    """Interpreter to run the hook with, preferring the project's virtualenv.

    The hook must keep working after the user closes the shell where they
    activated the venv, so the absolute interpreter path is baked into the
    command rather than relying on ``PATH``.
    """
    return platform_utils.python_executable(ROOT)


def hook_script_path() -> Path:
    return ROOT / "scripts" / "collect_usage.py"


def build_command(python_exe: Optional[str] = None, script: Optional[Path] = None) -> str:
    """The shell command Claude Code will run for each hook event.

    Claude Code runs hook commands through the platform's own shell, so both the
    separator style and the quoting are platform-dependent; both decisions live
    in ``platform_utils`` rather than being spelled out here.
    """
    exe = platform_utils.normalise_hook_path(python_exe or python_executable())
    target = platform_utils.normalise_hook_path(script or hook_script_path())
    return "%s %s %s" % (
        platform_utils.quote_hook_arg(exe),
        platform_utils.quote_hook_arg(target),
        MARKER,
    )


def is_ours(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("type") == "command"
        and MARKER in str(entry.get("command", ""))
    )


def _group_is_ours(group: Any) -> bool:
    """True when a hook group contains only our entries (safe to drop whole)."""
    if not isinstance(group, dict):
        return False
    entries = group.get("hooks")
    return isinstance(entries, list) and bool(entries) and all(is_ours(e) for e in entries)


def backup_settings(path: Path) -> Optional[Path]:
    """Copy ``settings.json`` next to itself with a timestamp. Never destructive."""
    path = Path(path)
    if not path.is_file():
        return None
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    target = path.with_name("%s.backup-cctracker-%s.json" % (path.stem, stamp))
    try:
        shutil.copy2(path, target)
        return target
    except OSError:
        return None


def _load_settings(path: Path) -> Dict[str, Any]:
    data = read_json(path, default=None)
    if data is None and Path(path).is_file():
        # A settings file we cannot parse is one we must not rewrite.
        raise ValueError(
            "%s exists but is not valid JSON; fix or move it before installing." % path
        )
    return data if isinstance(data, dict) else {}


def apply_install(settings: Dict[str, Any], command: str,
                  events: Tuple[str, ...] = HOOK_EVENTS) -> Tuple[Dict[str, Any], bool]:
    """Return ``(new_settings, changed)`` with our hook present exactly once.

    Existing hooks - ours from an older install, or anybody else's - are
    preserved; only a stale copy of *our* command is replaced.
    """
    updated = copy.deepcopy(settings)
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.json has a 'hooks' key that is not an object")

    changed = False
    for event in events:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = [] if groups is None else [groups]
            changed = True

        kept: List[Any] = []
        already = False
        for group in groups:
            if _group_is_ours(group):
                entries = group.get("hooks", [])
                # Keep one group of ours, refreshing the command if it drifted
                # (for example after the venv was created).
                if not already:
                    if entries[0].get("command") != command:
                        entries[0]["command"] = command
                        changed = True
                    entries[0]["timeout"] = HOOK_TIMEOUT
                    kept.append(group)
                    already = True
                else:
                    changed = True  # drop a duplicate left by an older install
                continue

            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                # A mixed group: strip only our entries out of it.
                mine = [e for e in group["hooks"] if is_ours(e)]
                if mine:
                    if not already:
                        mine[0]["command"] = command
                        mine[0]["timeout"] = HOOK_TIMEOUT
                        already = True
                        group["hooks"] = [e for e in group["hooks"] if not is_ours(e)] + [mine[0]]
                    else:
                        group["hooks"] = [e for e in group["hooks"] if not is_ours(e)]
                    changed = True
            kept.append(group)

        if not already:
            kept.append({"hooks": [{"type": "command", "command": command,
                                    "timeout": HOOK_TIMEOUT}]})
            changed = True

        hooks[event] = kept

    return updated, changed


def apply_uninstall(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Remove every hook entry of ours, leaving all other configuration alone."""
    updated = copy.deepcopy(settings)
    hooks = updated.get("hooks")
    if not isinstance(hooks, dict):
        return updated, False

    changed = False
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if _group_is_ours(group):
                changed = True
                continue
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                remaining = [e for e in group["hooks"] if not is_ours(e)]
                if len(remaining) != len(group["hooks"]):
                    changed = True
                    group["hooks"] = remaining
                if not remaining:
                    continue
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            # Only drop the event key if we are what emptied it.
            if groups and changed:
                hooks.pop(event, None)

    if not hooks and "hooks" in updated:
        updated.pop("hooks")
    return updated, changed


# --------------------------------------------------------------------------
# Public operations
# --------------------------------------------------------------------------
def install(path: Optional[Path] = None, command: Optional[str] = None) -> Dict[str, Any]:
    path = Path(path or claude_settings_path())
    settings = _load_settings(path)
    command = command or build_command()
    updated, changed = apply_install(settings, command)

    backup = None
    if changed:
        backup = backup_settings(path)
        if not write_json_atomic(path, updated):
            raise OSError("could not write " + str(path))

    return {
        "path": str(path),
        "changed": changed,
        "backup": str(backup) if backup else None,
        "command": command,
        "events": list(HOOK_EVENTS),
    }


def uninstall(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path or claude_settings_path())
    if not path.is_file():
        return {"path": str(path), "changed": False, "backup": None}
    settings = _load_settings(path)
    updated, changed = apply_uninstall(settings)

    backup = None
    if changed:
        backup = backup_settings(path)
        if not write_json_atomic(path, updated):
            raise OSError("could not write " + str(path))

    return {"path": str(path), "changed": changed, "backup": str(backup) if backup else None}


def installed_events(path: Optional[Path] = None) -> List[str]:
    """Events that currently have one of our hooks registered."""
    path = Path(path or claude_settings_path())
    settings = read_json(path, default={}) or {}
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return []
    found = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(entries, list) and any(is_ours(e) for e in entries):
                found.append(event)
                break
    return sorted(found)


def describe_installation() -> List[str]:
    path = claude_settings_path()
    events = installed_events(path)
    lines = [
        "platform        : %s" % platform_utils.os_label(),
        "settings file   : " + str(path),
    ]
    if events:
        lines.append("hooks installed : " + ", ".join(events))
    else:
        lines.append(
            "hooks installed : NO  (run %s)" % platform_utils.install_command()
        )
    lines.append("hook command    : " + build_command())
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tracker.hooks")
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument("--settings", type=Path, default=None,
                        help="settings.json to modify (default: ~/.claude/settings.json)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.action == "status":
        if args.json:
            print(json.dumps({"events": installed_events(args.settings),
                              "settings": str(args.settings or claude_settings_path()),
                              "command": build_command()}, indent=2))
        else:
            for line in describe_installation():
                print(line)
        return 0

    result = install(args.settings) if args.action == "install" else uninstall(args.settings)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    verb = "Installed" if args.action == "install" else "Removed"
    if result["changed"]:
        print("%s Claude Code tracker hooks in %s" % (verb, result["path"]))
        if result.get("backup"):
            print("Backup written to " + result["backup"])
    else:
        print("No change needed - %s already up to date." % result["path"])
    if args.action == "install":
        print("Events: " + ", ".join(result["events"]))
        print("Command: " + result["command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
