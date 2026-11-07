"""A KT server on demand: the one ``kt serve`` runs, started in the background when none is
(``kt-ui --daemon``). Started this way it stops by itself once unused (``--idle-exit``).
"""

import argparse
import os
import time

import httpx

from kohakuterrarium.cli.serve import (
    LOG_PATH,
    RUN_DIR,
    _apply_host_layered_config,
    _current_runtime,
    _is_pid_alive,
    _load_state,
    _remove_runtime_files,
    _save_state,
    _spawn_server_process,
    _wait_until_bound,
    _write_started_state,
)
from kohakuterrarium.utils.file.lock import FileLock, FileLockBusy

# a server started on demand (``kt-ui --daemon``, ``kt-ui attach``) stops this long after its
# last session has gone
ON_DEMAND_IDLE_EXIT = 120.0


def _loopback(url: str) -> str:
    """A server bound to every interface is reached on this machine's loopback."""
    return url.replace("://0.0.0.0", "://127.0.0.1").replace("://:::", "://[::1]:")


def _touch(url: str) -> bool:
    """One request to the server (it counts as use, so an idle one does not stop under the
    caller); whether it answered."""
    try:
        token = os.environ.get("KT_HOST_TOKEN")
        headers = {"X-KT-Host-Token": token} if token else {}
        httpx.get(
            f"{url}/api/sessions/active", headers=headers, timeout=5, trust_env=False
        )
        return True
    except httpx.HTTPError:
        return False


def _running(state: dict) -> dict:
    url = _loopback(str(state.get("url", "")))
    return {"url": url, "started": False, "version": str(state.get("version", ""))}


def ensure_server(idle_exit: float = ON_DEMAND_IDLE_EXIT) -> dict:
    """The running KT server, started in the background when none is (with the configured
    host and port, stopping itself once ``idle_exit`` seconds pass with no session and no
    use).

    Returns ``{"url", "started", "version"}``: ``started`` when this call started it, and the
    version it runs (to tell a server older than this install). Raises RuntimeError when it
    could not start. Two callers at once start one server (a lock in the run directory).
    """
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(RUN_DIR / "start.lock")
    deadline = time.time() + 90
    while True:
        try:
            lock.acquire()
            break
        except FileLockBusy:
            if time.time() > deadline:
                raise RuntimeError("another start of the KT server did not finish")
            time.sleep(0.2)
    try:
        runtime = _current_runtime()
        if runtime["alive"]:
            found = _running(runtime["state"])
            _touch(found["url"])
            return found
        if runtime["pid"]:
            _remove_runtime_files()
        args = argparse.Namespace(host="127.0.0.1", port=8001)
        _apply_host_layered_config(args)
        pid = _spawn_server_process(
            args.host, args.port, False, "INFO", idle_exit=idle_exit
        )
        _write_started_state(
            pid=pid, host=args.host, port=args.port, dev=False, log_level="INFO"
        )
        # marked as started on demand (``kt serve start`` / ``status`` say it stops itself)
        _save_state({**_load_state(), "idle_exit": idle_exit})
        status = _wait_until_bound(pid, timeout=60.0)
        if status == "dead":
            _remove_runtime_files()
            raise RuntimeError(f"the KT server did not start (log: {LOG_PATH})")
        found = _running(_load_state())
        if not found["url"]:
            found["url"] = _loopback(f"http://{args.host}:{args.port}")
        # bound is not answering yet on a slow start: wait for it to answer
        for _ in range(120):
            if _touch(found["url"]):
                return {**found, "started": True}
            if not _is_pid_alive(pid):
                break
            time.sleep(0.5)
        raise RuntimeError(f"the KT server is not answering (log: {LOG_PATH})")
    finally:
        lock.release()


__all__ = ["ON_DEMAND_IDLE_EXIT", "ensure_server"]
