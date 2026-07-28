"""Shared helpers for shell-like built-in tools."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from typing import Any


def windows_process_kwargs() -> dict[str, Any]:
    """Return spawn options that keep Windows children from opening console windows.

    ``CREATE_NO_WINDOW`` detaches the child from the parent console, so console
    control events no longer reach it; callers must terminate children
    explicitly (``terminate_process_tree`` uses ``taskkill``).
    """
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        ),
    }


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a process tree, escalating after bounded graceful waits."""
    try:
        if process.returncode is not None:
            return
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **windows_process_kwargs(),
            )
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
            except Exception:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
                return
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                except Exception:
                    process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            pass
