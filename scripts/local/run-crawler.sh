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
OWNER_USER_ID="${CRAWLER_OWNER_USER_ID:-0}"
FORCE="${FORCE:-}"

CRAWLER_DIR="$REPO_ROOT/crawler"
PYTHON_BIN="$CRAWLER_DIR/.venv/bin/python"

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

export DATABASE_URL S3_ENDPOINT S3_PUBLIC_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY S3_BUCKET S3_REGION

if [ "$OWNER_USER_ID" -gt 0 ] 2>/dev/null; then
    export CRAWLER_OWNER_USER_ID="$OWNER_USER_ID"
fi

# Validate required env (skip if owner mode — config comes from DB)
if [ "${CRAWLER_OWNER_USER_ID:-0}" -le 0 ] 2>/dev/null; then
    if [ -z "${TG_API_ID:-}" ] || [ -z "${TG_API_HASH:-}" ] || [ -z "${TG_PHONE:-}" ]; then
        echo "Error: Missing TG_API_ID/TG_API_HASH/TG_PHONE. Set them in .env.local or shell env." >&2
        exit 1
    fi
fi

# Single-instance guard
if [ -z "$FORCE" ]; then
    EXISTING_PID=""
    for pid_dir in /proc/[0-9]*/; do
        pid=$(basename "$pid_dir")
        cmd_file="$pid_dir/cmdline"
        if [ -r "$cmd_file" ]; then
            cmd=$(tr '\0' ' ' < "$cmd_file" 2>/dev/null || true)
            if echo "$cmd" | grep -q "main.py" && echo "$cmd" | grep -q "$CRAWLER_DIR"; then
                EXISTING_PID="$pid"
                break
            fi
        fi
    done

    if [ -z "$EXISTING_PID" ] && [ "$(uname)" = "Darwin" ]; then
        EXISTING_PID=$(ps aux | grep "main.py" | grep "$CRAWLER_DIR" | grep -v grep | awk '{print $2}' | head -1 || true)
    fi

    if [ -n "$EXISTING_PID" ]; then
        echo "Crawler appears to be running already (PID: $EXISTING_PID). Set FORCE=1 to bypass." >&2
        exit 1
    fi
fi

echo "Starting crawler (owner_user_id=${CRAWLER_OWNER_USER_ID:-0})..."
cd "$CRAWLER_DIR"
exec "$PYTHON_BIN" main.py
