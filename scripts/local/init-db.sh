#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DATABASE_URL="${DATABASE_URL:-postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler}"
PYTHON_BIN="$REPO_ROOT/web/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "Error: Python venv not found at $PYTHON_BIN. Run scripts/local/setup-python.sh first." >&2
    exit 1
fi

# Load .env / .env.local if present
for env_file in "$REPO_ROOT/.env" "$REPO_ROOT/.env.local"; do
    if [ -f "$env_file" ]; then
        set -a
        # shellcheck disable=SC1090
        source <(sed 's/#.*//; /^[[:space:]]*$/d' "$env_file")
        set +a
    fi
done

export DATABASE_URL

echo "Initializing database: $DATABASE_URL"
"$PYTHON_BIN" "$SCRIPT_DIR/init_db.py"
echo "Database initialized."
