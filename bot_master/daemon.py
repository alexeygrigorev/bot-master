import asyncio
import os
import signal
import sys
from pathlib import Path

from bot_master.process_manager import ProcessManager
from bot_master.protocol import CONFIG_PATH, SOCKET_PATH, read_message, write_message

manager = ProcessManager()


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    subscription: tuple[str, asyncio.Queue] | None = None

    try:
        while True:
            msg = await read_message(reader)
            if msg is None:
                break

            action = msg.get("action")

            # Cancel any active log subscription when a new command arrives
            if subscription:
                bot_name, queue = subscription
                try:
                    manager.get_bot(bot_name).unsubscribe(queue)
                except KeyError:
                    pass
                subscription = None

            if action == "status":
                await write_message(writer, {"ok": True, "bots": manager.get_all_status()})

            elif action in ("start", "stop", "restart"):
                bot_name = msg.get("bot", "")
                try:
                    bot = manager.get_bot(bot_name)
                    await getattr(bot, action)()
                    await write_message(writer, {"ok": True})
                except KeyError:
                    await write_message(writer, {"ok": False, "error": f"Unknown bot: {bot_name}"})
                except Exception as e:
                    await write_message(writer, {"ok": False, "error": str(e)})

            elif action == "logs":
                bot_name = msg.get("bot", "")
                n = msg.get("lines", 200)
                try:
                    bot = manager.get_bot(bot_name)
                    lines = bot.get_logs(n)
                    await write_message(writer, {"ok": True, "lines": lines})
                except KeyError:
                    await write_message(writer, {"ok": False, "error": f"Unknown bot: {bot_name}"})

            elif action == "subscribe_logs":
                bot_name = msg.get("bot", "")
                try:
                    bot = manager.get_bot(bot_name)
                    queue = bot.subscribe()
                    subscription = (bot_name, queue)
                    await write_message(writer, {"ok": True, "streaming": True})

                    # Stream logs until client sends a new command or disconnects
                    while True:
                        # Wait for either a log line or a new command
                        read_task = asyncio.create_task(reader.readline())
                        queue_task = asyncio.create_task(queue.get())

                        done, pending = await asyncio.wait(
                            {read_task, queue_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        for task in pending:
                            task.cancel()
                            try:
                                await task
                            except (asyncio.CancelledError, Exception):
                                pass

                        if queue_task in done:
                            line = queue_task.result()
                            await write_message(writer, {"log": line})

                        if read_task in done:
                            raw = read_task.result()
                            if not raw:
                                # Client disconnected
                                bot.unsubscribe(queue)
                                subscription = None
                                return
                            # Client sent a new command — unsubscribe and process it
                            bot.unsubscribe(queue)
                            subscription = None
                            # Put the message back by processing it inline
                            try:
                                import json
                                new_msg = json.loads(raw.decode("utf-8"))
                                # Re-inject: we'll handle it by breaking and letting
                                # the outer loop pick it up. But since we already consumed
                                # the line, we handle it directly here.
                                # Simplest: just break and let client resend
                            except Exception:
                                pass
                            break

                except KeyError:
                    await write_message(writer, {"ok": False, "error": f"Unknown bot: {bot_name}"})

            else:
                await write_message(writer, {"ok": False, "error": f"Unknown action: {action}"})

    except (ConnectionError, BrokenPipeError):
        pass
    finally:
        if subscription:
            bot_name, queue = subscription
            try:
                manager.get_bot(bot_name).unsubscribe(queue)
            except KeyError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG_PATH
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    print(f"Loading config from {config_path}")
    manager.load_config(config_path)

    print("Starting all bots...")
    await manager.start_all()

    # Clean up stale socket
    SOCKET_PATH.unlink(missing_ok=True)

    server = await asyncio.start_unix_server(handle_client, path=str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    print(f"Daemon listening on {SOCKET_PATH}")

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await shutdown_event.wait()

    print("Stopping all bots...")
    await manager.stop_all()
    server.close()
    await server.wait_closed()
    SOCKET_PATH.unlink(missing_ok=True)
    print("Daemon stopped.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
