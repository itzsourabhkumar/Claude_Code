#!/bin/sh
# Install the Claude Code token-tracking hooks on macOS.
#
#   ./scripts/install_macos.sh
#   ./scripts/install_macos.sh --backfill
#
# A convenience wrapper only: it finds an interpreter and runs
# scripts/install_hooks.py, which is the real, tested implementation and is
# identical on every platform. Any arguments are passed straight through.
#
# Homebrew is not required. The system python3 that ships with the Command Line
# Tools works, and so does a Homebrew or python.org install - whichever is found
# first is used.
set -eu

# Resolve the repository root without readlink -f, which macOS does not have.
CCTRACKER_ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
export CCTRACKER_ROOT_DIR
# shellcheck source=scripts/_python.sh
. "$CCTRACKER_ROOT_DIR/scripts/_python.sh"

exec "$PYTHON" "$CCTRACKER_ROOT_DIR/scripts/install_hooks.py" "$@"
