#!/usr/bin/env bash
set -euo pipefail

# Dev install: uses the local .venv for the daemon binary.
# For prod install, use: uvx bot-master install

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_BIN="${PROJECT_DIR}/.venv/bin"
SERVICE_NAME="bot-master"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER="$(whoami)"

# Ensure venv exists
if [ ! -f "${VENV_BIN}/bot-master-daemon" ]; then
    echo "Running uv sync first..."
    (cd "$PROJECT_DIR" && uv sync)
fi

echo "Installing systemd service (dev mode)..."
echo "  Project dir: ${PROJECT_DIR}"
echo "  User: ${CURRENT_USER}"
echo "  Service file: ${SERVICE_FILE}"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Bot Master Daemon - Telegram Bot Process Manager
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment=BOT_MASTER_LOG_DIR=${PROJECT_DIR}/logs
ExecStart=/bin/bash -lc '${VENV_BIN}/bot-master-daemon'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "Done! Service installed and started (dev mode)."
echo "  Uses: ${VENV_BIN}/bot-master-daemon"
echo "  Status:  sudo systemctl status ${SERVICE_NAME}"
echo "  Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  TUI:     cd ${PROJECT_DIR} && uv run bot-master"
