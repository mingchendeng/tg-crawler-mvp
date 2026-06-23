#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

for env_file in "$REPO_ROOT/.env" "$REPO_ROOT/.env.local"; do
    if [ -f "$env_file" ]; then
        set -a
        source <(sed 's/#.*//; /^[[:space:]]*$/d' "$env_file")
        set +a
    fi
done

TG_PROXY_HOST="${TG_PROXY_HOST:-127.0.0.1}"
TG_PROXY_PORT="${TG_PROXY_PORT:-7994}"
PROXY_LAUNCH_COMMAND="${PROXY_LAUNCH_COMMAND:-}"

# 1) Already listening?
if lsof -i :"$TG_PROXY_PORT" >/dev/null 2>&1; then
    echo "Proxy already running on $TG_PROXY_HOST:$TG_PROXY_PORT"
    exit 0
fi

# 2) If a launch command was configured, run it in background
if [ -n "$PROXY_LAUNCH_COMMAND" ]; then
    echo "Starting proxy via: $PROXY_LAUNCH_COMMAND"
    log_path="$REPO_ROOT/.local/runtime-logs/proxy.log"
    mkdir -p "$(dirname "$log_path")"
    eval "$PROXY_LAUNCH_COMMAND" >> "$log_path" 2>&1 &
    PROXY_PID=$!
    echo "Proxy launched (PID: $PROXY_PID)"

    for i in $(seq 1 15); do
        sleep 1
        if lsof -i :"$TG_PROXY_PORT" >/dev/null 2>&1; then
            echo "Proxy is now listening on $TG_PROXY_HOST:$TG_PROXY_PORT"
            exit 0
        fi
    done

    echo "Warning: Proxy command was launched but port $TG_PROXY_PORT is not listening yet."
    echo "Check log: $log_path"
    exit 1
fi

# 3) No proxy configured
echo "Not listening on $TG_PROXY_HOST:$TG_PROXY_PORT and no PROXY_LAUNCH_COMMAND set."
echo "Please start your proxy manually or set PROXY_LAUNCH_COMMAND in .env.local"
exit 1
