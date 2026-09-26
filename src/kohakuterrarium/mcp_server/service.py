"""Workspace-scoped commands for the owned MCP supervisor."""

import os
import secrets
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

from kohakuterrarium.mcp_server.connection import ConnectionStore, write_json
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def is_running(store: ConnectionStore) -> bool:
    lock = FileLock(store.instance_lock.path)
    try:
        lock.acquire()
    except FileLockBusy:
        return True
    else:
        lock.release()
        return False


@contextmanager
def _command(store: ConnectionStore):
    deadline = time.monotonic() + 40
    while True:
        try:
            store.command_lock.acquire()
            break
        except FileLockBusy:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Another MCP lifecycle command is still running; retry"
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        store.command_lock.release()


def status(store: ConnectionStore) -> dict:
    record = store.load()
    snapshot = store.runtime()
    running = is_running(store)
    if not snapshot:
        snapshot = {
            "state": "unresponsive",
            "local_ready": False,
            "public_ready": False,
            "error": "Runtime status unavailable; process ownership is determined by its lock",
        }
    if not running:
        snapshot.update(
            state="stopped",
            local_ready=False,
            public_ready=False,
            pid=None,
            tunnel_pid=None,
            tunnel_state="stopped" if record.tunnel == "ngrok" else "external",
        )
    elif snapshot.get("updated_at") and time.time() - snapshot["updated_at"] > 30:
        snapshot.update(state="unresponsive", public_ready=False)
    return {
        **snapshot,
        "running": running,
        "workspace": record.workspace,
        "public_origin": record.public_origin,
        "port": record.port,
        "tunnel": record.tunnel,
        "record_path": str(store.record_path),
    }


def start(store: ConnectionStore, *, wait: float = 30, **options) -> dict:
    if not 1 <= wait <= 120:
        raise ValueError("Startup wait must be between 1 and 120 seconds")
    with _command(store):
        if is_running(store):
            record = store.load()
            if options and store.configure(**options, persist=False) != record:
                raise ValueError(
                    "MCP instance is running; stop before changing its settings"
                )
            return status(store)
        record = store.configure(**options)
        if record.tunnel == "ngrok" and not shutil.which(record.ngrok_bin):
            raise ValueError(
                "ngrok executable not found; use --ngrok-bin or --tunnel external"
            )
        run_id = secrets.token_hex(16)
        write_json(
            store.runtime_path,
            {
                "run_id": run_id,
                "state": "starting",
                "updated_at": time.time(),
                "local_ready": False,
                "public_ready": False,
            },
        )
        log_path = store.directory / "server.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "kohakuterrarium.serving.mcp",
                    "--workspace",
                    store.workspace,
                    "--state-dir",
                    str(store.directory.parent),
                    "--run-id",
                    run_id,
                ],
                cwd=store.workspace,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
                close_fds=True,
            )
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            snapshot = store.runtime()
            if snapshot.get("run_id") == run_id and snapshot.get("state") in {
                "ready",
                "failed",
            }:
                return status(store)
            if process.poll() is not None:
                result = status(store)
                result["error"] = (
                    snapshot.get("error")
                    or "MCP supervisor exited during startup; inspect server.log"
                )
                return result
            time.sleep(0.1)
        return status(store)


def stop(store: ConnectionStore, *, wait: float = 20) -> dict:
    with _command(store):
        if not is_running(store):
            return status(store)
        snapshot = store.runtime()
        if not snapshot.get("run_id"):
            raise RuntimeError(
                "Instance lock is held without a runtime identity; no process was signalled"
            )
        write_json(store.stop_path, {"run_id": snapshot["run_id"]})
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not is_running(store):
                return status(store)
            time.sleep(0.1)
        raise RuntimeError(
            "Stop request is pending; instance still owns its lock. No PID was killed"
        )
