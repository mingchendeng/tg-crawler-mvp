#!/usr/bin/env bash
set -euo pipefail

# Idempotent deploy script used by CI/Actions and manual runs.
# - Copies .env.stored -> .env if needed
# - Ensures a virtualenv exists at venv (not .venv) and installs requirements
# - Restarts systemd unit tg-crawler-web.service

ROOT_DIR="/opt/tg-crawler"
VENV_DIR="$ROOT_DIR/venv"
PYTHON_BIN="$VENV_DIR/bin/python3"
PIP_BIN="$VENV_DIR/bin/pip"

echo "Starting deploy script at $(date -u)"
cd "$ROOT_DIR"

# Ensure .env exists
if [ -f .env.stored ] && [ ! -f .env ]; then
  echo "Copied .env.stored -> .env"
  cp .env.stored .env
  chmod 600 .env || true
fi

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "Upgrading pip and installing requirements"
"$PIP_BIN" install --upgrade pip setuptools wheel || true
if [ -f web/requirements.txt ]; then
  "$PIP_BIN" install -r web/requirements.txt
fi
if [ -f crawler/requirements.txt ]; then
  "$PIP_BIN" install -r crawler/requirements.txt
fi

echo "Restarting systemd service: tg-crawler-web.service"
sudo systemctl daemon-reload || true
sudo systemctl restart tg-crawler-web.service || true
sudo systemctl status tg-crawler-web.service --no-pager || true

echo "Deploy script finished at $(date -u)"
