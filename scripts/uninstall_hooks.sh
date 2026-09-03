#!/bin/sh
# Remove the Claude Code token-tracking hooks on Linux and macOS.
#
#   ./scripts/uninstall_hooks.sh
#   ./scripts/uninstall_hooks.sh --purge-data
#
# A convenience wrapper only: it finds an interpreter and runs
# scripts/uninstall_hooks.py, which is the real, tested implementation. Any
# arguments are passed straight through.
set -eu

# Resolve the repository root without readlink -f, which macOS does not have.
CCTRACKER_ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
export CCTRACKER_ROOT_DIR
# shellcheck source=scripts/_python.sh
. "$CCTRACKER_ROOT_DIR/scripts/_python.sh"

exec "$PYTHON" "$CCTRACKER_ROOT_DIR/scripts/uninstall_hooks.py" "$@"
