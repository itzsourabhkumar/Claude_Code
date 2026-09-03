# shellcheck shell=sh
#
# Shared interpreter discovery for the Linux/macOS wrapper scripts.
#
# Sourced, never executed. The caller sets $CCTRACKER_ROOT_DIR (the repository
# root) first; this file sets $PYTHON, or exits with a useful message when no
# suitable interpreter exists. Nothing else lives here: the install, uninstall
# and dashboard logic is all Python, so the shell wrappers stay thin and
# identical across distributions.

if [ -z "${CCTRACKER_ROOT_DIR:-}" ]; then
    echo "_python.sh: CCTRACKER_ROOT_DIR must be set before sourcing." >&2
    exit 1
fi

# Prefer the project virtualenv, so the tracker keeps working in a shell where
# nobody has activated it.
PYTHON=""
for _candidate in \
    "$CCTRACKER_ROOT_DIR/.venv/bin/python3" \
    "$CCTRACKER_ROOT_DIR/.venv/bin/python" \
    "$CCTRACKER_ROOT_DIR/venv/bin/python3" \
    "$CCTRACKER_ROOT_DIR/venv/bin/python"
do
    if [ -x "$_candidate" ]; then
        PYTHON="$_candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    for _name in python3 python; do
        if command -v "$_name" >/dev/null 2>&1; then
            PYTHON=$(command -v "$_name")
            break
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    echo "" >&2
    echo "  Python 3 was not found." >&2
    echo "" >&2
    echo "  Ubuntu/Debian : sudo apt install python3 python3-venv git" >&2
    echo "  Fedora        : sudo dnf install python3 git" >&2
    echo "  macOS         : xcode-select --install   (or: brew install python git)" >&2
    echo "" >&2
    exit 1
fi

# Refuse Python 2 and anything older than the supported range rather than
# failing later with a confusing syntax error.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
then
    echo "" >&2
    echo "  $PYTHON is too old. Python 3.9 or newer is required (3.11+ recommended)." >&2
    echo "" >&2
    exit 1
fi

export PYTHON
