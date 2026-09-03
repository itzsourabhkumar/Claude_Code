#!/usr/bin/env python
"""Claude Code hook entry point.

This is the exact command Claude Code runs on ``Stop`` and ``SessionEnd``. It is
deliberately tiny: add the repository to ``sys.path``, hand the payload to the
collector, and exit 0 no matter what happens.

It is invoked as::

    python scripts/collect_usage.py --cctracker

with the hook payload on stdin. The ``--cctracker`` marker is what
``tracker.hooks`` uses to recognise (and later remove) its own hooks without
touching anybody else's.

Nothing here can fail loudly: if the tracker is broken, Claude Code must carry
on exactly as if it were not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from tracker.collector import run_hook

        return run_hook()
    except Exception:
        # Import failed (moved repo, broken venv, partial checkout...). Stay
        # silent on stdout/stderr so Claude Code sees a clean, successful hook.
        try:
            sys.stdout.write("{}")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
