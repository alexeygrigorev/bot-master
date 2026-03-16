import asyncio
import logging
import os
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

LOG_DIR = Path(os.environ.get("BOT_MASTER_LOG_DIR", "logs"))


def _setup_file_logger(name: str, log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"bot.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_dir / f"{name}.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


@dataclass
class BotConfig:
    name: str
    directory: str
    command: str


class BotProcess:
    def __init__(self, config: BotConfig, log_dir: Path) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.status: str = "stopped"  # stopped, running, backoff
        self.log_buffer: deque[str] = deque(maxlen=5000)
        self.subscribers: set[asyncio.Queue] = set()
        self.restart_count: int = 0
        self._should_run: bool = False
        self._tasks: list[asyncio.Task] = []
        self._start_time: float = 0
        self._lock = asyncio.Lock()
        self._file_logger = _setup_file_logger(config.name, log_dir)

    async def start(self) -> None:
        async with self._lock:
            if self.status == "running":
                return
            self._should_run = True
            self.restart_count = 0
            await self._spawn()

    async def _spawn(self) -> None:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        try:
            self.process = await asyncio.create_subprocess_shell(
                self.config.command,
                cwd=self.config.directory,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        except Exception as e:
            self._log(f"[bot-master] Failed to start: {e}")
            self.status = "backoff"
            if self._should_run:
                asyncio.create_task(self._auto_restart(-1))
            return

        self.status = "running"
        self._start_time = time.monotonic()
        self._log(f"[bot-master] Started (pid={self.process.pid})")

        reader_task = asyncio.create_task(self._read_output())
        waiter_task = asyncio.create_task(self._wait())
        self._tasks = [reader_task, waiter_task]

    async def _read_output(self) -> None:
        assert self.process and self.process.stdout
        try:
            async for raw_line in self.process.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                self._log(line)
        except Exception:
            pass

    async def _wait(self) -> None:
        assert self.process
        code = await self.process.wait()
        ran_for = time.monotonic() - self._start_time

        if self._should_run:
            self._log(f"[bot-master] Exited with code {code}")
            if ran_for > 30:
                self.restart_count = 0
            self.status = "backoff"
            asyncio.create_task(self._auto_restart(code))
        else:
            self.status = "stopped"
            self._log(f"[bot-master] Stopped (exit code {code})")

    async def _auto_restart(self, code: int) -> None:
        delay = min(2 ** self.restart_count, 60)
        self.restart_count += 1
        self._log(f"[bot-master] Restarting in {delay}s (attempt {self.restart_count})...")
        await asyncio.sleep(delay)
        if self._should_run:
            await self._spawn()

    async def stop(self) -> None:
        async with self._lock:
            self._should_run = False
            if self.process and self.process.returncode is None:
                self._log("[bot-master] Stopping...")
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._log("[bot-master] Force killing...")
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        pass

            for task in self._tasks:
                task.cancel()
            self._tasks.clear()
            self.process = None
            self.status = "stopped"

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def get_status(self) -> dict:
        pid = self.process.pid if self.process and self.process.returncode is None else None
        uptime = None
        if self.status == "running" and self._start_time:
            uptime = int(time.monotonic() - self._start_time)
        return {
            "name": self.config.name,
            "status": self.status,
            "pid": pid,
            "restart_count": self.restart_count,
            "uptime": uptime,
        }

    def get_logs(self, n: int = 200) -> list[str]:
        lines = list(self.log_buffer)
        return lines[-n:]

    def _log(self, line: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{ts} {line}"
        self.log_buffer.append(entry)
        self._file_logger.info(entry)
        for q in list(self.subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass


class ProcessManager:
    def __init__(self, log_dir: Path | None = None) -> None:
        self.bots: dict[str, BotProcess] = {}
        self.log_dir = log_dir or LOG_DIR

    def load_config(self, path: Path) -> None:
        with open(path) as f:
            data = yaml.safe_load(f)

        for name, info in data["bots"].items():
            config = BotConfig(
                name=name,
                directory=info["directory"],
                command=info["command"],
            )
            self.bots[name] = BotProcess(config, self.log_dir)

    async def start_all(self) -> None:
        await asyncio.gather(*(bot.start() for bot in self.bots.values()))

    async def stop_all(self) -> None:
        await asyncio.gather(*(bot.stop() for bot in self.bots.values()))

    def get_bot(self, name: str) -> BotProcess:
        return self.bots[name]

    def get_all_status(self) -> list[dict]:
        return [bot.get_status() for bot in self.bots.values()]
