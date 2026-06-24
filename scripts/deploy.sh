#!/usr/bin/env bash
set -euo pipefail

# Idempotent deploy script used by CI/Actions and manual runs.
# - Copies .env.stored -> .env if needed
# - Ensures a working virtualenv exists at venv and installs requirements
# - Restarts systemd unit tg-crawler-web.service

ROOT_DIR="/opt/tg-crawler"
# Prefer new .venv layout; keep old venv only for backup/rollback
NEW_VENV_DIR="$ROOT_DIR/.venv"
OLD_VENV_DIR="$ROOT_DIR/venv"
VENV_DIR="$NEW_VENV_DIR"

SYSTEM_PYTHON="$(command -v python3 || true)"
if [ -z "$SYSTEM_PYTHON" ]; then
  echo "python3 not found on system; aborting"
  exit 1
fi

echo "Starting deploy script at $(date -u)"
cd "$ROOT_DIR"

# Create deploy log and redirect all output to it for artifact collection
LOG_DIR="$ROOT_DIR/deploy_logs"
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR" || true
LOGFILE="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ).log"
ln -sf "$LOGFILE" "$LOG_DIR/latest.log"
# Redirect stdout/stderr to logfile while still echoing to console
exec > >(tee -a "$LOGFILE") 2>&1


# Ensure .env exists (workflow now injects secrets into /opt/tg-crawler/.env)
if [ -f .env.stored ] && [ ! -f .env ]; then
  echo "Copied .env.stored -> .env"
  cp .env.stored .env
  chmod 600 .env || true
fi

# If an old "venv" exists and .venv does not, backup and remove the old one.
if [ -d "$OLD_VENV_DIR" ] && [ ! -d "$NEW_VENV_DIR" ]; then
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_TAR="$ROOT_DIR/venv-backup-$TS.tar.gz"
  echo "Backing up old venv to $BACKUP_TAR"
  # create a tar.gz snapshot of the old venv for safe rollback
  tar -czf "$BACKUP_TAR" -C "$ROOT_DIR" "$(basename "$OLD_VENV_DIR")" || true
  echo "Removing old venv at $OLD_VENV_DIR"
  rm -rf "$OLD_VENV_DIR" || true
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

# Ensure systemd unit points to the .venv python/uvicorn and listens on 127.0.0.1
SERVICE_PATH="/etc/systemd/system/tg-crawler-web.service"
if [ -f "$SERVICE_PATH" ]; then
  echo "Ensuring $SERVICE_PATH ExecStart uses $VENV_DIR and binds 127.0.0.1"
  sudo cp "$SERVICE_PATH" "$SERVICE_PATH.bak-$(date -u +%Y%m%dT%H%M%SZ)" || true
  # Replace ExecStart line to use the .venv uvicorn and 127.0.0.1
  sudo sed -i -E "s|^ExecStart=.*|ExecStart=${VENV_DIR}/bin/uvicorn main:app --host 127.0.0.1 --port 8080|g" "$SERVICE_PATH" || true
else
  echo "Systemd unit $SERVICE_PATH missing - creating a basic unit"
  sudo tee "$SERVICE_PATH" > /dev/null <<'UNIT'
[Unit]
Description=TG Crawler Web
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/tg-crawler/web
ExecStart=/opt/tg-crawler/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5
EnvironmentFile=/opt/tg-crawler/.env

[Install]
WantedBy=multi-user.target
UNIT
fi

echo "Reloading systemd and restarting tg-crawler-web.service"
sudo systemctl daemon-reload || true
sudo systemctl restart tg-crawler-web.service || true
sudo systemctl status tg-crawler-web.service --no-pager || true

# Ensure nginx site exists and proxies to 127.0.0.1:8080
NGINX_CONF="/etc/nginx/sites-available/tg-crawler.conf"
if [ ! -f "$NGINX_CONF" ]; then
  echo "Creating nginx conf $NGINX_CONF"
  sudo tee "$NGINX_CONF" > /dev/null <<'NGINXCONF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    client_max_body_size 50M;
}
NGINXCONF
  sudo ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/tg-crawler.conf || true
  sudo nginx -t || true
  sudo systemctl reload nginx || true
fi

echo "Restarting systemd service: tg-crawler-web.service"
sudo systemctl daemon-reload || true
sudo systemctl restart tg-crawler-web.service || true
sudo systemctl status tg-crawler-web.service --no-pager || true

echo "Deploy script finished at $(date -u)"
