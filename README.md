# Bot Master

A process manager for Telegram bots with a terminal UI. Runs as a background daemon (survives reboots via systemd) with a Textual TUI client for monitoring.

## Architecture

- **Daemon** (`bot-master-daemon`) — manages bot subprocesses, auto-restarts on crash (exponential backoff), buffers logs in memory and writes to disk. Communicates via Unix socket.
- **TUI Client** (`bot-master`) — connects to the daemon to view live status, stream logs, and send start/stop/restart commands. If the TUI crashes, bots keep running.

## Setup

```bash
cd ~/bots/bot-master
uv sync
```

## Configuration

Edit `bots.yaml`:

```yaml
bots:
  my-bot:
    directory: /path/to/bot
    command: uv run python main.py
```

## Usage

### Start the daemon manually

```bash
uv run bot-master-daemon
```

### Install as a systemd service (auto-start on boot, auto-restart on failure)

```bash
./install.sh
```

This generates the systemd unit from the current directory, enables and starts the service.

### Connect with the TUI

```bash
uv run bot-master
```

### TUI Keybindings

| Key | Action |
|-----|--------|
| `s` | Start selected bot |
| `x` | Stop selected bot |
| `r` | Restart selected bot |
| `a` | Start all bots |
| `z` | Stop all bots |
| `j`/`k` or arrows | Navigate bot list |
| `q` | Quit TUI (daemon keeps running) |

## Logs

Logs are stored in the `logs/` directory (one file per bot, 10 MB rotation with 5 backups):

```
logs/
  au-tomator.log
  zapier.log
  writing-assistant.log
```

The daemon also keeps the last 5000 lines per bot in memory for fast streaming to the TUI.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_MASTER_SOCK` | `/tmp/bot-master.sock` | Unix socket path |
| `BOT_MASTER_CONFIG` | `bots.yaml` | Config file path |
| `BOT_MASTER_LOG_DIR` | `logs` | Log directory path |
