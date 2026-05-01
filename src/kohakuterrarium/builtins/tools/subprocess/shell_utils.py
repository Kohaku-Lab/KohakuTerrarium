"""Shared helpers for shell-like built-in tools."""

import asyncio
import os
import signal
import sys


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess and its children best-effort."""
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
            )
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except Exception:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
                return
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
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
