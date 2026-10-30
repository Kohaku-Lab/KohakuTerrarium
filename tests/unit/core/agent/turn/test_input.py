"""Configured-input admission preserves serial turns and busy-safe commands."""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tui.input import TUIInput
from kohakuterrarium.builtins.user_commands.exit import ExitCommand
from kohakuterrarium.core.agent_input import drive_input
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    UserCommandResult,
)


class Command:
    aliases = ["aside"]

    def __init__(self, safe, calls):
        self.safe_while_busy = safe
        self.calls = calls

    async def execute(self, args, context):
        self.calls.append(args)
        return UserCommandResult(consumed=True)


@pytest.mark.asyncio
async def test_polling_admits_safe_alias_and_serializes_normal_commands():
    lines = asyncio.Queue()
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()
    module = TUIInput()
    module._running = True
    module._tui = SimpleNamespace(get_input=lambda prompt: lines.get())
    module.set_user_commands(
        {"btw": Command(True, calls), "clear": Command(False, calls)},
        UserCommandContext(),
    )
    # Each command owns only its own alias.
    module._command_alias_map = {"aside": "btw"}
    processed = []
    lock = asyncio.Lock()

    async def process(event):
        async with lock:
            processed.append(event.content)
            started.set()
            await release.wait()

    agent = SimpleNamespace(
        input=module, _running=True, _process_event=process, _processing_lock=lock
    )
    task = asyncio.create_task(drive_input(agent))
    try:
        await lines.put("main")
        await asyncio.wait_for(started.wait(), 1)
        await lines.put("/aside side")
        for _ in range(20):
            await asyncio.sleep(0)
        assert calls == ["side"]
        assert not task.done()
        await lines.put("next")
        await lines.put("/clear clear")
        await lines.put("/aside later")
        await lines.put("last")
        await lines.put("")
        for _ in range(20):
            await asyncio.sleep(0)
        assert calls == ["side", "later"]
        assert processed == ["main"]
        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
        assert processed == ["main", "next", "last"]
        assert calls == ["side", "later", "clear"]
        await lines.put("")
        await asyncio.wait_for(task, 1)
        assert module.command_idle_waiter is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_eof_during_deferred_command_does_not_drop_following_input():
    lines = asyncio.Queue()
    started, release = asyncio.Event(), asyncio.Event()
    processed = []

    class SlowCommand(Command):
        async def execute(self, args, context):
            started.set()
            await release.wait()
            return UserCommandResult(consumed=True)

    module = TUIInput()
    module._running = True
    module._tui = SimpleNamespace(get_input=lambda prompt: lines.get())
    module.set_user_commands({"slow": SlowCommand(False, [])}, UserCommandContext())

    async def process(event):
        processed.append(event.content)

    agent = SimpleNamespace(
        input=module,
        _running=True,
        _process_event=process,
        _processing_lock=asyncio.Lock(),
    )
    task = asyncio.create_task(drive_input(agent))
    try:
        await lines.put("/slow")
        await asyncio.wait_for(started.wait(), 1)
        await lines.put("after command")
        await lines.put("")
        for _ in range(20):
            await asyncio.sleep(0)
        assert module.exit_requested
        release.set()
        await asyncio.wait_for(task, 1)
        assert processed == ["after command"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_deferred_exit_reaps_pending_reader():
    lines = asyncio.Queue()
    module = TUIInput()
    module._running = True
    module._tui = SimpleNamespace(get_input=lambda prompt: lines.get())
    module.set_user_commands(
        {"exit": ExitCommand()}, UserCommandContext(input_module=module)
    )
    agent = SimpleNamespace(
        input=module, _running=True, _processing_lock=asyncio.Lock()
    )
    await lines.put("/exit")
    await asyncio.wait_for(drive_input(agent), 1)
    assert module.exit_requested
    assert module.command_idle_waiter is None


@pytest.mark.asyncio
async def test_failed_turn_cancels_pending_read_and_restores_waiter():
    lines = asyncio.Queue()
    module = TUIInput()
    module._running = True
    module._tui = SimpleNamespace(get_input=lambda prompt: lines.get())

    async def fail(event):
        raise ValueError("failed turn")

    agent = SimpleNamespace(
        input=module,
        _running=True,
        _process_event=fail,
        _processing_lock=asyncio.Lock(),
    )
    await lines.put("main")
    with pytest.raises(ValueError, match="failed turn"):
        await asyncio.wait_for(drive_input(agent), 1)
    assert module.command_idle_waiter is None


@pytest.mark.asyncio
async def test_startup_barrier_keeps_normal_input_queued_but_admits_btw():
    lines = asyncio.Queue()
    calls = []
    started, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    module = TUIInput()
    module._running = True
    module._tui = SimpleNamespace(get_input=lambda prompt: lines.get())
    module.set_user_commands({"btw": Command(True, calls)}, UserCommandContext())
    processed = []

    async def startup():
        started.set()
        await release.wait()

    async def process(event):
        assert settled.is_set()
        processed.append(event.content)

    agent = SimpleNamespace(
        input=module,
        _running=True,
        _process_event=process,
        _processing_lock=asyncio.Lock(),
        _fire_startup_trigger=startup,
        _startup_settled=settled,
    )
    task = asyncio.create_task(drive_input(agent, startup=True))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await lines.put("main")
        await lines.put("/btw question")
        for _ in range(20):
            await asyncio.sleep(0)
        assert calls == ["question"]
        assert not settled.is_set()
        assert processed == []
        release.set()
        await lines.put("")
        await asyncio.wait_for(task, 1)
        assert settled.is_set()
        assert processed == ["main"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
