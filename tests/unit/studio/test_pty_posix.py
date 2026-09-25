"""POSIX terminal lifecycle and nonblocking I/O regressions."""

import asyncio
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.studio.attach import pty_posix

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX I/O required")


@pytest.mark.parametrize(
    "loop_kind",
    [
        "asyncio",
        pytest.param(
            "uvloop",
            marks=pytest.mark.skipif(
                importlib.util.find_spec("uvloop") is None,
                reason="uvloop not installed",
            ),
        ),
    ],
)
@pytest.mark.parametrize(
    "mode",
    [
        "disconnect",
        "cancel",
        "cancel_twice",
        "initial_disconnect",
        "invalid_json",
        "resistant",
        "roundtrip",
        "fork_failure",
    ],
)
def test_real_terminal_shutdown_preserves_event_loop(tmp_path, mode, loop_kind):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.helpers.pty_lifecycle_probe",
            mode,
            str(tmp_path),
            loop_kind,
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.splitlines()[-1])
    assert not report["rescued"], report
    assert report["max_tick_gap"] < 0.75, report
    assert report["master_closed"] and report["slave_closed"], report
    assert report["pending_tasks"] == 0, report
    if mode != "fork_failure":
        assert report["reaped"], report
    if mode == "roundtrip":
        assert report["output_ok"], report
    if mode in {"resistant", "cancel_twice"}:
        assert report["exit_code"] == -signal.SIGKILL, report
    if mode in {"cancel", "cancel_twice"}:
        assert report["error"] == "CancelledError", report
        expected = "second" if mode == "cancel_twice" else "first"
        assert report["error_args"] == [f"{expected} terminal cancel"], report


async def test_nonblocking_write_preserves_bytes_under_backpressure():
    sender, receiver = socket.socketpair()
    sender.setblocking(False)
    receiver.setblocking(False)
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
    data = bytes(range(256)) * 8192
    task = asyncio.create_task(pty_posix._write_fd(sender.fileno(), data))
    try:
        await asyncio.sleep(0.03)
        assert not task.done()
        received = bytearray()
        loop = asyncio.get_running_loop()
        while len(received) < len(data):
            received.extend(await asyncio.wait_for(loop.sock_recv(receiver, 4096), 2))
        await asyncio.wait_for(task, 2)
        assert received == data
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        sender.close()
        receiver.close()


@pytest.mark.parametrize("writing", [False, True])
async def test_cancelled_io_can_be_reused(writing):
    sender, receiver = socket.socketpair()
    sender.setblocking(False)
    receiver.setblocking(False)
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
    operation = (
        pty_posix._write_fd(sender.fileno(), b"x" * 2**20)
        if writing
        else pty_posix._read_fd(sender.fileno())
    )
    task = asyncio.create_task(operation)
    try:
        await asyncio.sleep(0.03)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        if writing:
            while True:
                try:
                    receiver.recv(8192)
                except BlockingIOError:
                    break
            expected = b"next" * 65536
            task = asyncio.create_task(pty_posix._write_fd(sender.fileno(), expected))
            await asyncio.sleep(0.03)
            assert not task.done()
            received = bytearray()
            loop = asyncio.get_running_loop()
            while len(received) < len(expected):
                received.extend(
                    await asyncio.wait_for(loop.sock_recv(receiver, 4096), 2)
                )
            await asyncio.wait_for(task, 2)
            assert received == expected
        else:
            task = asyncio.create_task(pty_posix._read_fd(sender.fileno()))
            await asyncio.sleep(0.03)
            assert not task.done()
            receiver.send(b"next")
            assert await asyncio.wait_for(task, 2) == b"next"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        sender.close()
        receiver.close()
