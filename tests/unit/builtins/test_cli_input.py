"""Regression tests for terminal closure handling in CLI input."""

import asyncio
import io
import threading

from kohakuterrarium.builtins.inputs import cli
from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput


class _UnreadableStdin:
    def readline(self):
        raise OSError("stdin unavailable")


async def test_nonblocking_input_read_does_not_block_event_loop(monkeypatch):
    release = threading.Event()
    input_module = NonBlockingCLIInput()
    input_module._running = True
    monkeypatch.setattr(cli.sys, "platform", "linux")

    def blocking_read():
        release.wait(timeout=1.0)
        return None

    monkeypatch.setattr(input_module, "_try_read", blocking_read)

    task = asyncio.create_task(input_module.get_input())
    await asyncio.sleep(0.02)
    assert not task.done()

    release.set()
    assert await task is None


async def test_missing_stdin_is_terminal_eof_without_logging(monkeypatch):
    input_module = CLIInput()
    output = io.StringIO()
    errors = []
    monkeypatch.setattr(cli.sys, "stdin", None)
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True

    assert await input_module.get_input() is None
    assert output.getvalue() == "> "
    assert errors == []


async def test_stdin_eof_closes_without_logging(monkeypatch):
    input_module = CLIInput()
    output = io.StringIO()
    errors = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True

    assert await input_module.get_input() is None
    assert output.getvalue() == "> "
    assert errors == []


async def test_unexpected_readline_error_is_logged_only_once(monkeypatch):
    input_module = CLIInput()
    output = io.StringIO()
    errors = []
    monkeypatch.setattr(cli.sys, "stdin", _UnreadableStdin())
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True
    assert await input_module.get_input() is None

    assert output.getvalue() == "> "
    assert len(errors) == 1
    assert errors[0][1]["error"] == "stdin unavailable"


async def test_unexpected_executor_read_error_is_logged_only_once(monkeypatch):
    input_module = CLIInput()
    errors = []

    def fail_read():
        raise RuntimeError("broken input")

    monkeypatch.setattr(input_module, "_read_line", fail_read)
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True
    assert await input_module.get_input() is None
    assert len(errors) == 1
    assert errors[0][1]["error"] == "broken input"


async def test_non_blocking_missing_stdin_closes_without_repolling(monkeypatch):
    input_module = NonBlockingCLIInput(timeout=1)
    output = io.StringIO()
    errors = []
    monkeypatch.setattr(cli.sys, "stdin", None)
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True
    assert await input_module.get_input() is None
    assert output.getvalue() == ""
    assert errors == []


async def test_non_blocking_readline_failure_is_logged_only_once(monkeypatch):
    input_module = NonBlockingCLIInput(timeout=1)
    output = io.StringIO()
    errors = []
    monkeypatch.setattr(cli.sys, "stdin", _UnreadableStdin())
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(
        cli.logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await input_module.start()
    assert await input_module.get_input() is None
    assert input_module.exit_requested is True
    assert await input_module.get_input() is None
    assert output.getvalue() == "> "
    assert len(errors) == 1
    assert errors[0][1]["error"] == "stdin unavailable"


async def test_non_blocking_timeouts_start_only_one_daemon_reader(monkeypatch):
    input_module = NonBlockingCLIInput(timeout=0)
    output = io.StringIO()
    readers = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name
            readers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.threading, "Thread", FakeThread)

    await input_module.start()
    for _ in range(3):
        assert await input_module.get_input() is None

    assert len(readers) == 1
    assert readers[0].daemon is True


async def test_non_blocking_stop_does_not_start_or_restart_reader(monkeypatch):
    input_module = NonBlockingCLIInput(timeout=0)
    readers = []

    class FakeThread:
        def __init__(self, **kwargs):
            readers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.threading, "Thread", FakeThread)

    await input_module.start()
    assert await input_module.get_input() is None
    assert len(readers) == 1

    await input_module.stop()
    assert await input_module.get_input() is None
    assert len(readers) == 1

    stopped_input = NonBlockingCLIInput(timeout=0)
    await stopped_input.stop()
    assert await stopped_input.get_input() is None
    assert len(readers) == 1
