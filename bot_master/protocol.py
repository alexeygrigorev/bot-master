import asyncio
import json
import os
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("BOT_MASTER_SOCK", "/tmp/bot-master.sock"))
CONFIG_PATH = Path(os.environ.get("BOT_MASTER_CONFIG", "bots.yaml"))


async def read_message(reader: asyncio.StreamReader) -> dict | None:
    try:
        line = await reader.readline()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, ConnectionError):
        return None


async def write_message(writer: asyncio.StreamWriter, msg: dict) -> None:
    data = json.dumps(msg) + "\n"
    writer.write(data.encode("utf-8"))
    await writer.drain()
