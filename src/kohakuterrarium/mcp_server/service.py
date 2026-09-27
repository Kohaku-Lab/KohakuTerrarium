"""Configuration-environment commands for the owned MCP supervisor."""

import os
import secrets
import subprocess
import sys
import time

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.records import lifecycle_command, write_json
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def is_running(store: EndpointStore) -> bool:
    lock = FileLock(store.instance_lock.path)
    try:
        lock.acquire()
    except FileLockBusy:
        return True
    else:
        lock.release()
        return False


def status(store: EndpointStore) -> dict:
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
    configured = store.configuration_summary(record)
    active = None
    if running:
        try:
            active = store.active_summary(snapshot.get("run_id", ""))
        except ValueError:
            snapshot.update(
                state="unresponsive",
                public_ready=False,
                error="Active configuration unavailable; stop and start this endpoint once",
            )
    changes = (
        {
            key: {"running": active[key], "configured": value}
            for key, value in configured.items()
            if active[key] != value
        }
        if active is not None
        else {}
    )
    effective = active if running else configured
    return {
        **snapshot,
        "running": running,
        "home_dir": record.home_dir,
        "public_origin": effective["public_origin"] if effective else None,
        "port": effective["port"] if effective else None,
        "tunnel": effective["tunnel"] if effective else None,
        "active": active,
        "configured": configured,
        "pending_changes": changes,
        "restart_required": bool(changes) if not running or active else None,
        "record_path": str(store.record_path),
    }


def connection_url(store: EndpointStore, *, configured: bool = False) -> str:
    if not configured and is_running(store):
        return store.load_active(store.runtime().get("run_id", "")).url
    return store.load().url


def rotate(store: EndpointStore) -> dict:
    """Replace a stopped endpoint's secret, preserving its other settings."""
    with lifecycle_command(store.command_lock):
        lock = FileLock(store.instance_lock.path)
        try:
            lock.acquire()
        except FileLockBusy:
            raise RuntimeError(
                "MCP instance lock is busy; stop this endpoint before rotating, "
                "or retry if it is already stopped"
            ) from None
        try:
            record = store.load()
            record.secret = secrets.token_urlsafe(32)
            write_json(store.record_path, record.model_dump())
            return {
                "state": "stopped",
                "running": False,
                "rotated": True,
                "home_dir": record.home_dir,
            }
        finally:
            lock.release()


def start(store: EndpointStore, *, wait: float = 30) -> dict:
    if not 1 <= wait <= 120:
        raise ValueError("Startup wait must be between 1 and 120 seconds")
    with lifecycle_command(store.command_lock):
        if is_running(store):
            return status(store)
        record = store.load()
        store.validate(record)
        run_id = secrets.token_hex(16)
        store.save_active(run_id, record)
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
                    *store.server_arguments,
                    "--run-id",
                    run_id,
                ],
                cwd=store.process_directory,
                env={
                    **os.environ,
                    "KT_CONFIG_DIR": str(store.home_dir),
                },
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
        while process.poll() is None:
            try:
                store.instance_lock.acquire()
            except FileLockBusy:
                snapshot = store.runtime()
                if (
                    snapshot.get("run_id") == run_id
                    and snapshot.get("ownership_acquired") is True
                ):
                    return status(store)
                time.sleep(0.01)
            else:
                try:
                    # Fence a late handoff while reaping this exact child process.
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                finally:
                    store.instance_lock.release()
                result = status(store)
                result["error"] = (
                    "MCP supervisor did not acquire its instance lock before startup timed out"
                )
                return result
        return status(store)


def stop(store: EndpointStore, *, wait: float = 20) -> dict:
    with lifecycle_command(store.command_lock):
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
