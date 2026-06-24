#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/tg-crawler}"
LOG_DIR="$ROOT_DIR/.local/runtime-logs"
DEPLOY_LOG_DIR="$ROOT_DIR/deploy_logs"
SESSION_DIR="$ROOT_DIR/crawler/session"
TMP_MEDIA_DIR="/tmp/tg_media"
PIP_CACHE_DIR="${HOME}/.cache/pip"

echo "=== Cleanup started at $(date -u) ==="
TOTAL_FREED=0

# 1. Runtime logs: truncate to last 500 lines
if [ -d "$LOG_DIR" ]; then
    for f in "$LOG_DIR"/*.log; do
        [ -f "$f" ] || continue
        lines=$(wc -l < "$f" 2>/dev/null || echo 0)
        if [ "$lines" -gt 500 ]; then
            tail -500 "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
            freed=$(( lines - 500 ))
            # rough estimate: ~100 bytes/line
            TOTAL_FREED=$(( TOTAL_FREED + freed * 100 ))
            echo "  Truncated $(basename "$f"): $lines -> 500 lines (~$((freed*100/1024)) KB freed)"
        fi
    done
fi

# 2. Temp media files older than 1 day
if [ -d "$TMP_MEDIA_DIR" ]; then
    count=$(find "$TMP_MEDIA_DIR" -type f -mtime +1 2>/dev/null | wc -l || true)
    if [ "$count" -gt 0 ]; then
        size=$(du -sh "$TMP_MEDIA_DIR" 2>/dev/null | awk '{print $1}' || true)
        find "$TMP_MEDIA_DIR" -type f -mtime +1 -delete 2>/dev/null || true
        echo "  Removed $count stale temp media files (was $size)"
        TOTAL_FREED=$(( TOTAL_FREED + 1 ))
    fi
    # Remove empty dirs
    find "$TMP_MEDIA_DIR" -type d -empty -delete 2>/dev/null || true
fi

# 3. Stale QR session files older than 1 hour
if [ -d "$SESSION_DIR" ]; then
    count=$(find "$SESSION_DIR" -name 'tmp*.session' -mmin +60 2>/dev/null | wc -l || true)
    if [ "$count" -gt 0 ]; then
        size=$(du -sh "$SESSION_DIR" 2>/dev/null | awk '{print $1}' || true)
        find "$SESSION_DIR" -name 'tmp*.session' -mmin +60 -delete 2>/dev/null || true
        echo "  Removed $count stale QR session files (was $size)"
        TOTAL_FREED=$(( TOTAL_FREED + 1 ))
    fi
fi

# 4. Deploy logs: keep last 10
if [ -d "$DEPLOY_LOG_DIR" ]; then
    count=$(find "$DEPLOY_LOG_DIR" -maxdepth 1 -name '*.log' ! -name 'latest.log' 2>/dev/null | wc -l || true)
    if [ "$count" -gt 10 ]; then
        find "$DEPLOY_LOG_DIR" -maxdepth 1 -name '*.log' ! -name 'latest.log' -printf '%T@ %p\0' 2>/dev/null | \
            sort -z -rn | tail -z -n +11 | cut -z -f2- | xargs -0 rm -f 2>/dev/null || true
        echo "  Pruned deploy logs: kept last 10 of $count"
        TOTAL_FREED=$(( TOTAL_FREED + 1 ))
    fi
fi

# 5. Python __pycache__ directories
find "$ROOT_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
echo "  Cleared Python __pycache__"

# 6. Pip cache (only if >100MB)
if [ -d "$PIP_CACHE_DIR" ]; then
    pip_cache_size=$(du -sm "$PIP_CACHE_DIR" 2>/dev/null | awk '{print $1}' || echo 0)
    if [ "$pip_cache_size" -gt 100 ]; then
        pip cache purge 2>/dev/null || true
        echo "  Purged pip cache (was ${pip_cache_size}MB)"
    fi
fi

# 7. Old venv backup tarballs (keep newest 2)
find "$ROOT_DIR" -maxdepth 1 -name 'venv-backup-*.tar.gz' -printf '%T@ %p\0' 2>/dev/null | \
    sort -z -rn | tail -z -n +3 | cut -z -f2- | xargs -0 rm -f 2>/dev/null || true

echo "=== Cleanup finished at $(date -u) ==="
