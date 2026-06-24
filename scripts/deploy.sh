#!/usr/bin/env bash
set -euo pipefail

# Idempotent deploy script used by CI/Actions and manual runs.
# - Copies .env.stored -> .env if needed
# - Ensures a working virtualenv exists at venv and installs requirements
# - Restarts systemd unit tg-crawler-web.service

ROOT_DIR="/opt/tg-crawler"
VENV_DIR="$ROOT_DIR/venv"

SYSTEM_PYTHON="$(command -v python3 || true)"
if [ -z "$SYSTEM_PYTHON" ]; then
  echo "python3 not found on system; aborting"
  exit 1
fi

echo "Starting deploy script at $(date -u)"
cd "$ROOT_DIR"

# Ensure .env exists
if [ -f .env.stored ] && [ ! -f .env ]; then
  echo "Copied .env.stored -> .env"
  cp .env.stored .env
  chmod 600 .env || true
fi

create_venv() {
  echo "(re)creating virtualenv at $VENV_DIR using $SYSTEM_PYTHON"
  rm -rf "$VENV_DIR" || true
  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  # Try to upgrade pip inside new venv
  "$VENV_DIR/bin/python3" -m pip install --upgrade pip setuptools wheel || true
}

# Ensure venv exists and has pip; if not, recreate it
if [ ! -d "$VENV_DIR" ]; then
  create_venv
else
  if [ ! -x "$VENV_DIR/bin/python3" ] || [ ! -f "$VENV_DIR/bin/pip" ]; then
    echo "Existing venv is missing python/pip; recreating venv"
    create_venv
  else
    echo "Found existing venv; ensuring pip is available"
    # try to ensurepip or upgrade pip if needed
    if ! "$VENV_DIR/bin/python3" -m pip --version >/dev/null 2>&1; then
      echo "Bootstrapping pip in venv"
      "$VENV_DIR/bin/python3" -m ensurepip --upgrade || "$VENV_DIR/bin/python3" -m pip install --upgrade pip setuptools wheel || true
    else
      "$VENV_DIR/bin/python3" -m pip install --upgrade pip setuptools wheel || true
    fi
  fi
fi

PIP_BIN="$VENV_DIR/bin/pip"

echo "Installing requirements via $PIP_BIN"
if [ -f web/requirements.txt ]; then
  "$PIP_BIN" install -r web/requirements.txt || true
fi
if [ -f crawler/requirements.txt ]; then
  "$PIP_BIN" install -r crawler/requirements.txt || true
fi

echo "Restarting systemd service: tg-crawler-web.service"
sudo systemctl daemon-reload || true
sudo systemctl restart tg-crawler-web.service || true
sudo systemctl status tg-crawler-web.service --no-pager || true

echo "Deploy script finished at $(date -u)"
