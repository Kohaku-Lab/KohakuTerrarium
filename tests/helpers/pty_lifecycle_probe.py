"""Exercise a real POSIX terminal in a process isolated from the test runner."""

import asyncio
import importlib
import json
import os
import pty
import signal
import sys
import threading
import time
from pathlib import Path

from fastapi import WebSocketDisconnect

from kohakuterrarium.studio.attach import pty_posix


def main() -> None:
    mode, directory, loop_kind = sys.argv[1:]
    state = {"rescued": False}
    finished = threading.Event()
    real_openpty, real_fork, real_waitpid = pty.openpty, os.fork, os.waitpid

    def waitpid(pid, flags):
        result = real_waitpid(pid, flags)
        if result[0]:
            state["exit_code"] = os.waitstatus_to_exitcode(result[1])
        return result

    def openpty():
        master, slave = real_openpty()
        state.update(master=master, slave=slave, tty=os.ttyname(slave))
        return master, slave

    def rescue():
        if finished.wait(4):
            return
        state["rescued"] = True
        # A blocked macOS close needs the pending read released before kill/reap.
        try:
            fd = os.open(state["tty"], os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                os.write(fd, b"\n")
            finally:
                os.close(fd)
        except OSError:
            pass
        try:
            if os.waitpid(state["child"], os.WNOHANG)[0] == 0:
                os.kill(state["child"], signal.SIGKILL)
        except (ChildProcessError, ProcessLookupError):
            pass

    watchdog = None

    def fork():
        nonlocal watchdog
        if mode == "fork_failure":
            raise OSError("synthetic fork failure")
        child = real_fork()
        if child:
            state["child"] = child
            watchdog = threading.Thread(target=rescue, daemon=True)
            watchdog.start()
        return child

    pty.openpty, os.fork, os.waitpid = openpty, fork, waitpid
    pty_posix._find_shell = lambda: "/bin/bash"
    Path(directory, ".bash_profile").write_text(
        "PS1='kt-test> '\nunset PROMPT_COMMAND\n"
    )
    os.environ["HOME"] = directory
    for key in ("BASH_ENV", "ENV", "PROMPT_COMMAND"):
        os.environ.pop(key, None)

    class Socket:
        def __init__(self):
            self.ready = asyncio.Event()
            self.marker = asyncio.Event()
            self.output = ""
            self.requests = 0

        async def send_json(self, message):
            if mode == "initial_disconnect":
                raise WebSocketDisconnect()
            self.output += message["data"]
            if "kt-test> " in self.output:
                self.ready.set()
            if "KT-PTY-OK\r\n" in self.output:
                self.marker.set()

        async def receive_text(self):
            await self.ready.wait()
            self.requests += 1
            if (
                mode in {"roundtrip", "resistant", "cancel_twice"}
                and self.requests == 1
            ):
                return json.dumps({"type": "resize", "rows": 37, "cols": 91})
            if (
                mode in {"roundtrip", "resistant", "cancel_twice"}
                and self.requests == 2
            ):
                command = "printf '\\113\\124\\055\\120\\124\\131\\055\\117\\113\\n'; stty size; "
                command = (
                    "trap '' HUP TERM; " + command + "while :; do :; done"
                    if mode != "roundtrip"
                    else command + "exit"
                )
                return json.dumps({"type": "input", "data": command + "\n"})
            if mode == "roundtrip":
                await asyncio.Future()
            if mode in {"resistant", "cancel_twice"}:
                await self.marker.wait()
            # Let the idle read settle after the shell's last output.
            await asyncio.sleep(0.05)
            if mode in {"cancel", "cancel_twice"}:
                if mode == "cancel_twice":
                    asyncio.get_running_loop().call_later(
                        0.1, session.cancel, "second terminal cancel"
                    )
                session.cancel("first terminal cancel")
                await asyncio.Future()
            if mode == "invalid_json":
                return "{"
            raise WebSocketDisconnect()

    async def run():
        nonlocal session
        socket = Socket()
        ticks = []

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        async def observed_session():
            try:
                await pty_posix.pty_session(socket, directory)
            except BaseException as exc:
                state["error"] = type(exc).__name__
                state["error_args"] = list(exc.args)
                raise

        pulse = asyncio.create_task(heartbeat())
        session = asyncio.create_task(observed_session())
        try:
            await session
        except (
            WebSocketDisconnect,
            asyncio.CancelledError,
            ValueError,
            OSError,
        ):
            pass
        ticks.append(time.monotonic())
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        state["max_tick_gap"] = max(
            (b - a for a, b in zip(ticks, ticks[1:])), default=0
        )
        state["output_ok"] = (
            "KT-PTY-OK\r\n" in socket.output and "37 91" in socket.output
        )
        state["marker_seen"] = socket.marker.is_set()
        state["pending_tasks"] = len(asyncio.all_tasks()) - 1
        for name in ("master", "slave"):
            try:
                os.fstat(state[name])
            except OSError:
                state[name + "_closed"] = True
            else:
                state[name + "_closed"] = False
                os.close(state[name])
        if "child" in state:
            try:
                child, _ = os.waitpid(state["child"], os.WNOHANG)
            except ChildProcessError:
                state["reaped"] = True
            else:
                state["reaped"] = False
                if child == 0:
                    os.kill(state["child"], signal.SIGKILL)
                    os.waitpid(state["child"], 0)
        finished.set()

    session = None
    try:
        if loop_kind == "uvloop":
            importlib.import_module("uvloop").run(run())
        else:
            asyncio.run(run())
    finally:
        finished.set()
        if watchdog:
            watchdog.join(timeout=1)
    print(json.dumps(state))


if __name__ == "__main__":
    main()
