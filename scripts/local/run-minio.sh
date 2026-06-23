#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MINIO_EXE="${MINIO_EXE:-minio}"
DATA_DIR="${MINIO_DATA_DIR:-$REPO_ROOT/.local/minio/data}"
ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
API_PORT="${MINIO_API_PORT:-9000}"
CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"
FORCE="${FORCE:-}"

mkdir -p "$DATA_DIR"

# Resolve minio binary
MINIO_BIN=""
if [ -x "$MINIO_EXE" ]; then
    MINIO_BIN="$MINIO_EXE"
elif command -v minio &>/dev/null; then
    MINIO_BIN="$(command -v minio)"
else
    echo "Error: minio binary not found. Install from https://min.io/download or set MINIO_EXE." >&2
    exit 1
fi

# Single-instance guard
if [ -z "$FORCE" ]; then
    EXISTING_PID=""

    # Linux: check /proc
    if [ -d /proc ]; then
        for pid_dir in /proc/[0-9]*/; do
            pid=$(basename "$pid_dir")
            cmd_file="$pid_dir/cmdline"
            if [ -r "$cmd_file" ]; then
                cmd=$(tr '\0' ' ' < "$cmd_file" 2>/dev/null || true)
                if echo "$cmd" | grep -q "minio" && echo "$cmd" | grep -q ":$API_PORT"; then
                    EXISTING_PID="$pid"
                    break
                fi
            fi
        done
    fi

    # macOS fallback
    if [ -z "$EXISTING_PID" ] && [ "$(uname)" = "Darwin" ]; then
        EXISTING_PID=$(ps aux | grep "minio" | grep ":$API_PORT" | grep -v grep | awk '{print $2}' | head -1 || true)
    fi

    if [ -n "$EXISTING_PID" ]; then
        echo "MinIO appears to be running already (PID: $EXISTING_PID). Set FORCE=1 to bypass." >&2
        exit 1
    fi
fi

export MINIO_ROOT_USER="$ROOT_USER"
export MINIO_ROOT_PASSWORD="$ROOT_PASSWORD"

echo "Starting MinIO (API:$API_PORT Console:$CONSOLE_PORT)..."
exec "$MINIO_BIN" server "$DATA_DIR" --address ":$API_PORT" --console-address ":$CONSOLE_PORT"
