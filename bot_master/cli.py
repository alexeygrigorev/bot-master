import os
import socket
import sys
from pathlib import Path

from bot_master.protocol import SOCKET_PATH


def _daemon_is_running() -> bool:
    """Check if the daemon is reachable via its Unix socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(str(SOCKET_PATH))
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def _find_daemon_bin() -> str | None:
    for candidate in [
        Path.home() / ".local" / "bin" / "bot-master-daemon",
        Path("/usr/local/bin/bot-master-daemon"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _generate_service(
    work_dir: Path, config_path: Path, daemon_bin: str | None, user: str
) -> str:
    if daemon_bin:
        exec_start = f"{daemon_bin} {config_path}"
    else:
        exec_start = f"{Path.home()}/.local/bin/bot-master-daemon {config_path}"

    # Capture current PATH so child processes (bot commands using uv, etc.) work
    current_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    home = Path.home()
    # Ensure ~/.local/bin is in PATH for uv
    if f"{home}/.local/bin" not in current_path:
        current_path = f"{home}/.local/bin:{current_path}"

    return (
        "[Unit]\n"
        "Description=Bot Master Daemon - Telegram Bot Process Manager\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={work_dir}\n"
        f"Environment=BOT_MASTER_LOG_DIR={work_dir}/logs\n"
        f'Environment="PATH={current_path}"\n'
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def run_install() -> None:
    home = Path.home()
    default_dir = home / "bots" / "bot-master"

    print("Bot Master - Install Wizard")
    print("=" * 40)
    print()

    # Ask for working directory
    print("Where should bot-master store its config and logs?")
    dir_input = input(f"  Directory [{default_dir}]: ").strip()
    work_dir = Path(dir_input) if dir_input else default_dir
    work_dir = work_dir.expanduser().resolve()

    # Create directory structure
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "logs").mkdir(exist_ok=True)

    # Create bots.yaml if it doesn't exist
    config_path = work_dir / "bots.yaml"
    if config_path.exists():
        print(f"\n  Config already exists: {config_path}")
    else:
        config_path.write_text(
            "bots:\n"
            "  # example:\n"
            "  #   directory: /path/to/bot\n"
            "  #   command: uv run python main.py\n"
        )
        print(f"\n  Created config: {config_path}")

    print(f"  Logs directory: {work_dir / 'logs'}")

    daemon_bin = _find_daemon_bin()

    # Ask about systemd
    print()
    install_systemd = input("Install systemd service? [Y/n]: ").strip().lower()
    if install_systemd in ("", "y", "yes"):
        user = os.environ.get("USER", "nobody")
        service_content = _generate_service(work_dir, config_path, daemon_bin, user)
        service_path = work_dir / "bot-master.service"
        service_path.write_text(service_content)
        print(f"\n  Generated: {service_path}")

    # Print next steps
    print()
    print("=" * 40)
    print("Next steps:")
    print()

    step = 1
    print(f"  {step}. Edit your bot config:")
    print(f"     {config_path}")
    print()
    step += 1

    if not daemon_bin:
        print(f"  {step}. Install bot-master permanently:")
        print(f"     uv tool install bot-master")
        print()
        step += 1

    if install_systemd in ("", "y", "yes"):
        print(f"  {step}. Install the systemd service:")
        print(f"     sudo cp {work_dir}/bot-master.service /etc/systemd/system/")
        print(f"     sudo systemctl daemon-reload")
        print(f"     sudo systemctl enable --now bot-master")
        print()
        step += 1

    print(f"  {step}. Connect with the TUI:")
    print(f"     bot-master")
    print()


def show_not_running() -> None:
    print("Bot Master - daemon is not running")
    print()
    print("  To install and set up bot-master:")
    print("    uvx bot-master install")
    print()
    print("  Or start the daemon manually:")
    print("    bot-master-daemon [path/to/bots.yaml]")
    print()
    print("  If already installed as a systemd service:")
    print("    sudo systemctl start bot-master")
    print()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        run_install()
        return

    if not _daemon_is_running():
        show_not_running()
        return

    from bot_master.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
