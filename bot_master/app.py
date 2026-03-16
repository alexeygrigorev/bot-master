import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from bot_master.protocol import SOCKET_PATH, read_message, write_message


class DaemonClient:
    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False

    async def send(self, msg: dict) -> dict | None:
        if not self.writer or not self.reader:
            return None
        async with self._lock:
            try:
                await write_message(self.writer, msg)
                return await read_message(self.reader)
            except (ConnectionError, BrokenPipeError, OSError):
                return None

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass


class BotItem(Static):
    selected = reactive(False)

    def __init__(self, bot_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bot_name = bot_name
        self.bot_status = "stopped"
        self.bot_pid: int | None = None
        self.bot_uptime: int | None = None
        self.bot_restarts: int = 0

    class Selected(Message):
        def __init__(self, bot_name: str) -> None:
            super().__init__()
            self.bot_name = bot_name

    def on_click(self) -> None:
        self.post_message(self.Selected(self.bot_name))

    def render(self) -> str:
        status = self.bot_status
        if status == "running":
            indicator = "[green]●[/]"
            extra = ""
            if self.bot_uptime is not None:
                m, s = divmod(self.bot_uptime, 60)
                h, m = divmod(m, 60)
                extra = f" [dim]{h:02d}:{m:02d}:{s:02d}[/]"
        elif status == "backoff":
            indicator = "[yellow]●[/]"
            extra = f" [dim]restart #{self.bot_restarts}[/]"
        else:
            indicator = "[dim]●[/]"
            extra = ""

        sel = "▸ " if self.selected else "  "
        return f"{sel}{indicator} {self.bot_name}{extra}"

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "--selected")


class BotMasterApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Bot Master"

    BINDINGS = [
        Binding("s", "start_bot", "Start"),
        Binding("x", "stop_bot", "Stop"),
        Binding("r", "restart_bot", "Restart"),
        Binding("a", "start_all", "Start All"),
        Binding("z", "stop_all", "Stop All"),
        Binding("j", "next_bot", "Next", show=False),
        Binding("k", "prev_bot", "Prev", show=False),
        Binding("down", "next_bot", "Next", show=False),
        Binding("up", "prev_bot", "Prev", show=False),
        Binding("q", "quit", "Quit"),
    ]

    selected_bot: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self.client = DaemonClient()
        self.bot_names: list[str] = []
        self._log_task: asyncio.Task | None = None
        self._connected = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield Static("Bots", id="sidebar-title")
                yield Vertical(id="bot-list")
            with Vertical(id="log-container"):
                yield Static("Select a bot", id="log-title")
                yield RichLog(id="log-view", highlight=True, markup=True)
        yield Footer()

    async def on_mount(self) -> None:
        connected = await self.client.connect()
        if not connected:
            log_view = self.query_one("#log-view", RichLog)
            log_view.write("[red]Cannot connect to daemon.[/]")
            log_view.write(f"[dim]Is bot-master-daemon running? Socket: {SOCKET_PATH}[/]")
            log_view.write("")
            log_view.write("[dim]Start it with: bot-master-daemon[/]")
            return

        self._connected = True
        resp = await self.client.send({"action": "status"})
        if resp and resp.get("ok"):
            bot_list = self.query_one("#bot-list", Vertical)
            for bot_info in resp["bots"]:
                name = bot_info["name"]
                self.bot_names.append(name)
                item = BotItem(name, id=f"bot-{name}")
                item.bot_status = bot_info["status"]
                item.bot_pid = bot_info.get("pid")
                item.bot_uptime = bot_info.get("uptime")
                item.bot_restarts = bot_info.get("restart_count", 0)
                await bot_list.mount(item)

            if self.bot_names:
                self.selected_bot = self.bot_names[0]

        self.set_interval(2, self._poll_status)

    async def _poll_status(self) -> None:
        if not self._connected:
            return
        resp = await self.client.send({"action": "status"})
        if not resp or not resp.get("ok"):
            return
        for bot_info in resp["bots"]:
            name = bot_info["name"]
            try:
                item = self.query_one(f"#bot-{name}", BotItem)
                item.bot_status = bot_info["status"]
                item.bot_pid = bot_info.get("pid")
                item.bot_uptime = bot_info.get("uptime")
                item.bot_restarts = bot_info.get("restart_count", 0)
                item.refresh()
            except Exception:
                pass

    def on_bot_item_selected(self, event: BotItem.Selected) -> None:
        self.selected_bot = event.bot_name

    async def watch_selected_bot(self, bot_name: str) -> None:
        if not bot_name or not self._connected:
            return

        # Update selection UI
        for name in self.bot_names:
            try:
                item = self.query_one(f"#bot-{name}", BotItem)
                item.selected = name == bot_name
            except Exception:
                pass

        # Update title
        title = self.query_one("#log-title", Static)
        title.update(f"Logs: {bot_name}")

        # Cancel existing log stream
        if self._log_task:
            self._log_task.cancel()
            self._log_task = None

        # Fetch recent logs
        log_view = self.query_one("#log-view", RichLog)
        log_view.clear()

        # We need a fresh connection for log streaming since the protocol
        # uses subscribe_logs which holds the connection
        resp = await self.client.send({"action": "logs", "bot": bot_name, "lines": 500})
        if resp and resp.get("ok"):
            for line in resp["lines"]:
                log_view.write(line)

        # Start streaming in a separate connection
        self._log_task = asyncio.create_task(self._stream_logs(bot_name))

    async def _stream_logs(self, bot_name: str) -> None:
        try:
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            await write_message(writer, {"action": "subscribe_logs", "bot": bot_name})
            resp = await read_message(reader)
            if not resp or not resp.get("ok"):
                return

            log_view = self.query_one("#log-view", RichLog)
            while True:
                msg = await read_message(reader)
                if msg is None:
                    break
                if "log" in msg:
                    log_view.write(msg["log"])

        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def action_start_bot(self) -> None:
        if self.selected_bot and self._connected:
            await self.client.send({"action": "start", "bot": self.selected_bot})

    async def action_stop_bot(self) -> None:
        if self.selected_bot and self._connected:
            await self.client.send({"action": "stop", "bot": self.selected_bot})

    async def action_restart_bot(self) -> None:
        if self.selected_bot and self._connected:
            await self.client.send({"action": "restart", "bot": self.selected_bot})

    async def action_start_all(self) -> None:
        if self._connected:
            for name in self.bot_names:
                await self.client.send({"action": "start", "bot": name})

    async def action_stop_all(self) -> None:
        if self._connected:
            for name in self.bot_names:
                await self.client.send({"action": "stop", "bot": name})

    def action_next_bot(self) -> None:
        if not self.bot_names:
            return
        try:
            idx = self.bot_names.index(self.selected_bot)
            idx = (idx + 1) % len(self.bot_names)
        except ValueError:
            idx = 0
        self.selected_bot = self.bot_names[idx]

    def action_prev_bot(self) -> None:
        if not self.bot_names:
            return
        try:
            idx = self.bot_names.index(self.selected_bot)
            idx = (idx - 1) % len(self.bot_names)
        except ValueError:
            idx = 0
        self.selected_bot = self.bot_names[idx]

    async def action_quit(self) -> None:
        if self._log_task:
            self._log_task.cancel()
        await self.client.close()
        self.exit()


def main() -> None:
    app = BotMasterApp()
    app.run()


if __name__ == "__main__":
    main()
