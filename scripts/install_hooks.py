#!/usr/bin/env python3
"""Install the Claude Code token-tracking hooks. Works on every platform.

This is the canonical installer. The ``.ps1`` and ``.sh`` scripts beside it do
nothing except locate an interpreter and run this file, so there is one
implementation of the install logic and one place where it is tested.

    python  scripts/install_hooks.py          # Windows
    python3 scripts/install_hooks.py          # Linux / macOS

    --backfill              also import the Claude Code history already on disk
    --settings <path>       write to a specific settings.json
    --python <path>         pin a specific interpreter into the hook command
    --quiet                 only report problems

What it does, in order:

1. work out which OS this is and where Claude Code keeps its configuration
   (``~/.claude/settings.json``, or ``CLAUDE_CONFIG_DIR`` if set - the same
   mechanism on Windows, Linux and macOS);
2. back up that file, timestamped, before touching it;
3. add this project's ``Stop`` and ``SessionEnd`` hooks, leaving every unrelated
   hook and every other setting exactly as they were;
4. report what changed.

It is idempotent: running it twice leaves exactly one hook per event, and the
second run reports "no change needed".
"""

from __future__ import annotations

import argparse
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="%s scripts/install_hooks.py" % platform_utils.python_command_name(),
        description="Install the Claude Code token-tracking hooks (all platforms).",
    )
    parser.add_argument("--settings", type=Path, default=None,
                        help="settings.json to modify (default: Claude Code's own)")
    parser.add_argument("--python", dest="python_exe", default=None,
                        help="interpreter to bake into the hook command")
    parser.add_argument("--backfill", action="store_true",
                        help="import the Claude Code session history already on this machine")
    parser.add_argument("--quiet", action="store_true", help="only report problems")
    args = parser.parse_args(argv)

    quiet = args.quiet
    config = load_config()
    config.ensure_directories()

    settings_path = Path(args.settings) if args.settings else hooks.claude_settings_path()
    python_exe = args.python_exe or hooks.python_executable()

    if args.python_exe and not Path(args.python_exe).is_file():
        print("Interpreter not found: %s" % args.python_exe, file=sys.stderr)
        return 2

    _print("", quiet)
    _print("Claude Code Token Usage Tracker - hook installation", quiet)
    _print("", quiet)
    _print("  platform     : %s" % platform_utils.os_label(), quiet)
    _print("  project      : %s" % ROOT, quiet)
    _print("  python       : %s" % python_exe, quiet)
    _print("  settings     : %s" % settings_path, quiet)

    # A missing CLI is worth saying out loud, but it is not a reason to refuse:
    # the hooks are read from the settings file whenever Claude Code next starts,
    # so installing before (or after) the CLI is perfectly valid.
    if platform_utils.find_claude_executable() is None:
        _print("", quiet)
        _print("  note: the 'claude' command was not found on PATH. The hooks will", quiet)
        _print("        still be written and will take effect once Claude Code runs.", quiet)

    command = hooks.build_command(python_exe=python_exe)
    try:
        result = hooks.install(settings_path, command=command)
    except ValueError as exc:
        # An unparseable settings.json is refused rather than overwritten.
        print("", file=sys.stderr)
        print("Refusing to modify the settings file: %s" % exc, file=sys.stderr)
        return 1
    except PermissionError:
        print("", file=sys.stderr)
        print("Permission denied writing %s" % settings_path, file=sys.stderr)
        print("Check the file's ownership and permissions, then try again.", file=sys.stderr)
        return 1
    except OSError as exc:
        print("", file=sys.stderr)
        print("Could not write %s: %s" % (settings_path, exc), file=sys.stderr)
        return 1

    _print("", quiet)
    if result["changed"]:
        _print("  installed    : %s" % ", ".join(result["events"]), quiet)
        if result.get("backup"):
            _print("  backup       : %s" % result["backup"], quiet)
    else:
        _print("  no change needed - the hooks are already up to date.", quiet)

    if args.backfill:
        _print("", quiet)
        _print("Importing existing Claude Code history...", quiet)
        from tracker.cli import main as cli_main

        code = cli_main(["backfill"])
        if code != 0:
            return code

    _print("", quiet)
    _print("Done. Tracking starts with your next Claude Code session.", quiet)
    _print("Start the dashboard with:  %s" % platform_utils.dashboard_launcher(), quiet)
    _print("                      or:  %s" % platform_utils.server_command(), quiet)
    _print("", quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
