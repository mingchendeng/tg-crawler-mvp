#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DATABASE_URL="${DATABASE_URL:-postgresql://tguser:tgpwd@127.0.0.1:5432/tg_crawler}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_PUBLIC_ENDPOINT="${S3_PUBLIC_ENDPOINT:-}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
S3_BUCKET="${S3_BUCKET:-tg-crawler-media-ffe95227}"
S3_REGION="${S3_REGION:-ap-east-1}"
ADMIN_SECRET="${ADMIN_SECRET:-change-me-in-production}"
PORT="${PORT:-8080}"
FORCE="${FORCE:-}"

WEB_DIR="$REPO_ROOT/web"
PYTHON_BIN="$WEB_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "Error: Python venv not found at $PYTHON_BIN. Run scripts/local/setup-python.sh first." >&2
    exit 1
fi

# Load .env / .env.local
for env_file in "$REPO_ROOT/.env" "$REPO_ROOT/.env.local"; do
    if [ -f "$env_file" ]; then
        set -a
        source <(sed 's/#.*//; /^[[:space:]]*$/d' "$env_file")
        set +a
    fi
done

export DATABASE_URL S3_ENDPOINT S3_PUBLIC_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY S3_BUCKET S3_REGION ADMIN_SECRET

# Single-instance guard (skip if FORCE=1)
if [ -z "$FORCE" ]; then
    EXISTING_PID=""
    for pid_dir in /proc/[0-9]*/; do
        pid=$(basename "$pid_dir")
        cmd_file="$pid_dir/cmdline"
        if [ -r "$cmd_file" ]; then
            cmd=$(tr '\0' ' ' < "$cmd_file" 2>/dev/null || true)
            if echo "$cmd" | grep -q "uvicorn" && echo "$cmd" | grep -q "main:app" && echo "$cmd" | grep -q "$WEB_DIR"; then
                EXISTING_PID="$pid"
                break
            fi
        fi
    done

    # macOS fallback: use ps
    if [ -z "$EXISTING_PID" ] && [ "$(uname)" = "Darwin" ]; then
        EXISTING_PID=$(ps aux | grep "uvicorn.*main:app" | grep "$WEB_DIR" | grep -v grep | awk '{print $2}' | head -1 || true)
    fi

    if [ -n "$EXISTING_PID" ]; then
        echo "Web appears to be running already (PID: $EXISTING_PID). Set FORCE=1 to bypass." >&2
        exit 1
    fi
fi

echo "Starting web on port $PORT..."
cd "$WEB_DIR"
exec "$PYTHON_BIN" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload
