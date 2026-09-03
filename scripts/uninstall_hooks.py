#!/usr/bin/env python3
"""Remove the Claude Code token-tracking hooks. Works on every platform.

    python  scripts/uninstall_hooks.py        # Windows
    python3 scripts/uninstall_hooks.py        # Linux / macOS

    --settings <path>   remove from a specific settings.json
    --purge-data        also delete the collected usage data (asks first)
    --yes               answer yes to the --purge-data confirmation
    --quiet             only report problems

Only hook entries carrying this project's ``--cctracker`` marker are removed.
Every other hook you have configured, and every other setting in the file, is
copied through untouched, and the file is backed up before the change.

Collected usage data under ``data/`` is left alone unless ``--purge-data`` is
given, because it is the one thing here that cannot be regenerated.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import hooks, platform_utils  # noqa: E402
from tracker.config import load_config  # noqa: E402


def _print(message: str = "", quiet: bool = False) -> None:
    if not quiet:
        print(message)


def _purge(config, quiet: bool, assume_yes: bool) -> None:
    """Delete generated data after an explicit confirmation."""
    targets = [config.data_dir, config.reports_dir, config.logs_dir]
    existing = [path for path in targets if Path(path).exists()]
    if not existing:
        _print("  no collected data to remove.", quiet)
        return

    print("")
    print("  This will permanently delete:")
    for path in existing:
        print("    %s" % path)
    if not assume_yes:
        try:
            answer = input("  Type 'delete' to confirm: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "delete":
            print("  Left the data in place.")
            return

    for path in existing:
        try:
            shutil.rmtree(path)
            _print("  removed %s" % path, quiet)
        except OSError as exc:
            print("  could not remove %s: %s" % (path, exc), file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="%s scripts/uninstall_hooks.py" % platform_utils.python_command_name(),
        description="Remove the Claude Code token-tracking hooks (all platforms).",
    )
    parser.add_argument("--settings", type=Path, default=None,
                        help="settings.json to modify (default: Claude Code's own)")
    parser.add_argument("--purge-data", action="store_true",
                        help="also delete collected usage data, reports and logs")
    parser.add_argument("--yes", action="store_true",
                        help="skip the --purge-data confirmation prompt")
    parser.add_argument("--quiet", action="store_true", help="only report problems")
    args = parser.parse_args(argv)

    quiet = args.quiet
    config = load_config()
    settings_path = Path(args.settings) if args.settings else hooks.claude_settings_path()

    _print("", quiet)
    _print("Claude Code Token Usage Tracker - hook removal", quiet)
    _print("", quiet)
    _print("  platform     : %s" % platform_utils.os_label(), quiet)
    _print("  settings     : %s" % settings_path, quiet)

    try:
        result = hooks.uninstall(settings_path)
    except ValueError as exc:
        print("", file=sys.stderr)
        print("Refusing to modify the settings file: %s" % exc, file=sys.stderr)
        return 1
    except PermissionError:
        print("", file=sys.stderr)
        print("Permission denied writing %s" % settings_path, file=sys.stderr)
        return 1
    except OSError as exc:
        print("", file=sys.stderr)
        print("Could not write %s: %s" % (settings_path, exc), file=sys.stderr)
        return 1

    _print("", quiet)
    if result["changed"]:
        _print("  removed this project's hooks; every other hook was left in place.", quiet)
        if result.get("backup"):
            _print("  backup       : %s" % result["backup"], quiet)
    else:
        _print("  nothing to remove - no tracker hooks were installed.", quiet)

    if args.purge_data:
        _purge(config, quiet, args.yes)
    else:
        _print("", quiet)
        _print("  Collected usage data under %s was left untouched." % config.data_dir, quiet)
        _print("  Add --purge-data to delete it as well.", quiet)

    _print("", quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
