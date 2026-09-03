#!/bin/sh
# Start the Claude Code token usage dashboard on Linux and macOS.
#
#   ./start_dashboard.sh                start on the configured port
#   ./start_dashboard.sh --port 9000    start on another port
#   ./start_dashboard.sh --no-browser   do not open a browser window
#
# A convenience wrapper only: it finds an interpreter and runs server.py, which
# is the real implementation on every platform. Any arguments are passed
# straight through. The server binds to loopback only and needs no dependencies.
#
# If the file is not executable after cloning:  chmod +x start_dashboard.sh
set -eu

CCTRACKER_ENTRY="$0"
export CCTRACKER_ENTRY
# shellcheck source=scripts/_python.sh
. "$(dirname -- "$0")/scripts/_python.sh"

echo ""
echo "  Starting Claude Code Token Usage dashboard..."
echo ""

cd "$CCTRACKER_ROOT_DIR"
exec "$PYTHON" server.py "$@"
