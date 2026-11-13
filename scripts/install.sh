#!/bin/sh
# Install minibot into an isolated virtual environment.
#
# Usage:
#   ./scripts/install.sh              # install into ~/.local/share/minibot/venv
#   MINIBOT_PREFIX=/opt/minibot ./scripts/install.sh
#
# The script never touches the system Python and never needs root. It installs
# the package from this checkout, so the `minibot` command always runs the code
# in the working tree (re-run it after pulling).

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(dirname -- "$SCRIPT_DIR")

PREFIX=${MINIBOT_PREFIX:-"${XDG_DATA_HOME:-$HOME/.local/share}/minibot"}
VENV="$PREFIX/venv"
BIN_DIR="$PREFIX/bin"

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "error: $1 is required but was not found on PATH" >&2
        exit 1
    }
}

need python3

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
if [ "$PY_OK" != "1" ]; then
    echo "error: minibot needs Python 3.10 or newer (found $(python3 -V 2>&1))" >&2
    exit 1
fi

echo "==> creating virtual environment at $VENV"
python3 -m venv "$VENV"

echo "==> installing minibot from $REPO_DIR"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$REPO_DIR"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/minibot" "$BIN_DIR/minibot"

echo
echo "Installed: $BIN_DIR/minibot"
echo "Add it to your PATH if needed:"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
echo
echo "Next: start Ollama, then run:"
echo "  minibot --help"
