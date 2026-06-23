#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

for dir in web crawler; do
    VENV_DIR="$REPO_ROOT/$dir/.venv"
    PYTHON_BIN="$VENV_DIR/bin/python"

    if [ -x "$PYTHON_BIN" ]; then
        echo "[$dir] venv already exists: $PYTHON_BIN"
        continue
    fi

    echo "[$dir] Creating venv..."
    python3 -m venv "$VENV_DIR"
    "$PYTHON_BIN" -m pip install --upgrade pip
    "$PYTHON_BIN" -m pip install -r "$REPO_ROOT/$dir/requirements.txt"
    echo "[$dir] Done."
done

echo "All Python environments ready."
