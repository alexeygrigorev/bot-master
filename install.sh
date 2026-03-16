#!/usr/bin/env bash
set -euo pipefail

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

echo "Installing systemd service..."
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
ExecStart=${VENV_BIN}/bot-master-daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "Done! Service installed and started."
echo "  Status:  sudo systemctl status ${SERVICE_NAME}"
echo "  Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  TUI:     cd ${PROJECT_DIR} && uv run bot-master"
