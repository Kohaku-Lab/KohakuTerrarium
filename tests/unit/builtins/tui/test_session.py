"""Tests for shared TUI session state."""

import asyncio
from collections.abc import Callable
from typing import Any

from textual.containers import VerticalScroll

from kohakuterrarium.builtins.tui.app import _safe_id
from kohakuterrarium.builtins.tui.session import TUISession


class _RunningApp:
    is_running = True

    def __init__(self, active: str) -> None:
        self.active = active
        self.reconciled: list[list[str]] = []
        self.command_input = _CommandInput()

    def get_active_tab_name(self) -> str:
        return self.active

    def reconcile_terrarium_tabs(self, tabs: list[str]) -> None:
        self.reconciled.append(tabs)

    def call_later(self, callback: Callable[..., Any], *args: Any) -> bool:
        callback(*args)
        return True

    def query_one(self, selector: str, _widget_type: type) -> Any:
        assert selector == "#input-box"
        return self.command_input


class _CommandInput:
    def __init__(self) -> None:
        self.command_names: list[str] = []
        self.refreshes = 0

    def on_text_area_changed(self) -> None:
        self.refreshes += 1


class _Command:
    def __init__(self, *aliases: str) -> None:
        self.aliases = list(aliases)


class _ModalApp:
    is_running = True

    def __init__(self) -> None:
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.dismiss_callbacks: list[Callable[[Any], None]] = []

    def call_later(self, callback: Callable[[], None]) -> bool:
        callback()
        return True

    def push_screen(self, _modal: Any, callback: Callable[[Any], None]) -> None:
        self.dismiss_callbacks.append(callback)

    def exit(self) -> None:
        self.is_running = False


class _CommandAgent:
    def __init__(self, commands: dict[str, _Command]) -> None:
        self.commands = commands
        self.listeners: list[Callable[[dict], None]] = []

    def list_user_commands(self) -> dict[str, _Command]:
        return dict(self.commands)

    def add_user_command_listener(self, listener: Callable[[dict], None]) -> None:
        self.listeners.append(listener)

    def replace_commands(self, commands: dict[str, _Command]) -> None:
        self.commands = commands
        for listener in self.listeners:
            listener(commands)


def test_set_terrarium_tabs_reconciles_running_app_and_preserves_active() -> None:
    session = TUISession()
    session.set_terrarium_tabs(["root", "review-worker"])
    app = _RunningApp(active="review-worker")
    session._app = app  # type: ignore[assignment]

    session.set_terrarium_tabs(["root", "new_worker", "review-worker"])

    assert session._active_target == "review-worker"
    assert app.reconciled == [["root", "new_worker", "review-worker"]]

    app.active = "review-worker"
    session.set_terrarium_tabs(["root", "new_worker"])

    assert session._active_target == "root"
    assert app.reconciled[-1] == ["root", "new_worker"]


def test_command_hints_follow_active_tab_and_runtime_plugin_changes() -> None:
    root = _CommandAgent({"help": _Command("h"), "goal": _Command()})
    worker = _CommandAgent({"help": _Command("h"), "review": _Command("rv")})
    agents = {"root": root, "worker": worker}
    session = TUISession()
    session.host_agent = root
    session.resolve_tab_agent = agents.get
    app = _RunningApp(active="root")
    session._app = app  # type: ignore[assignment]

    session.watch_command_agent("root", root)
    session.watch_command_agent("worker", worker)
    session.watch_command_agent("root", root)
    session.refresh_command_hints_for_tab("root")
    assert app.command_input.command_names == ["goal", "h", "help"]
    assert len(root.listeners) == 1

    root.replace_commands({"help": _Command("h")})
    assert app.command_input.command_names == ["h", "help"]

    app.active = "worker"
    session.refresh_command_hints_for_tab("worker")
    assert app.command_input.command_names == ["h", "help", "review", "rv"]

    root.replace_commands({"goal": _Command()})
    assert app.command_input.command_names == ["h", "help", "review", "rv"]
    worker.replace_commands({"deploy": _Command("d")})
    assert app.command_input.command_names == ["d", "deploy"]


async def test_background_tab_culling_keeps_target_history_count() -> None:
    session = TUISession(max_chat_widgets=2, cull_keep=1)
    session.set_terrarium_tabs(["alice", "bob"])
    await session.start()
    assert session._app is not None

    async with session._app.run_test(size=(100, 40)) as pilot:
        session.set_active_target("alice")
        for index in range(3):
            session.add_system_notice(f"background-{index}", target="bob")
            await pilot.pause()

        bob_chat = session._app.query_one(f"#chat-{_safe_id('bob')}", VerticalScroll)
        assert len(bob_chat.children) == 2
        assert session._culled_count == {"bob": 2}


class TestModalShutdown:
    async def test_stop_settles_pending_modal_defaults(self) -> None:
        session = TUISession()
        app = _ModalApp()
        session._app = app  # type: ignore[assignment]

        selection = asyncio.create_task(session.show_selection_modal("Pick", []))
        confirmation = asyncio.create_task(session.show_confirm_modal("Continue?"))
        await asyncio.sleep(0)

        session.stop()

        assert await asyncio.wait_for(selection, 0.1) is None
        assert await asyncio.wait_for(confirmation, 0.1) is False
        assert session._pending_modal_defaults == {}

    async def test_dismiss_result_wins_before_shutdown(self) -> None:
        session = TUISession()
        app = _ModalApp()
        session._app = app  # type: ignore[assignment]

        selection = asyncio.create_task(session.show_selection_modal("Pick", []))
        await asyncio.sleep(0)
        app.dismiss_callbacks[0]("chosen")

        assert await selection == "chosen"
        session.stop()
