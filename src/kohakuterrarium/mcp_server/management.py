"""Local-only registry control, acknowledged by the owning endpoint process."""

import asyncio
import json
import time
import uuid
from pathlib import Path

from kohakuterrarium.mcp_server.connection import write_json
from kohakuterrarium.mcp_server.service import lifecycle_command
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def manage(store, operation, *, name=None, path=None, force=False, wait=30):
    """Apply one local registry command, or report an unacknowledged request."""
    # The endpoint has its own cwd; bind CLI paths before crossing the process boundary.
    if path is not None:
        path = Path(path).expanduser().resolve()
    with lifecycle_command(store):
        lock = FileLock(store.instance_lock.path)
        try:
            lock.acquire()
        except FileLockBusy:
            run_id = store.runtime().get("run_id")
            if not run_id:
                raise RuntimeError("Endpoint ownership unavailable; retry")
            previous = _read(store.control_path)
            response = _read(store.response_path)
            if previous.get("run_id") == run_id and previous.get(
                "request_id"
            ) != response.get("request_id"):
                raise RuntimeError(
                    "A workspace command is still pending; inspect workspace list before retrying"
                )
            request = {
                "run_id": run_id,
                "request_id": uuid.uuid4().hex,
                "operation": operation,
                "name": name,
                "path": str(path) if path else None,
                "force": force,
            }
            write_json(store.control_path, request)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                response = _read(store.response_path)
                if response.get("request_id") == request["request_id"]:
                    if response.get("error"):
                        raise ValueError(response["error"])
                    return response["result"]
                time.sleep(0.05)
            raise RuntimeError(
                "Workspace command outcome is unconfirmed; inspect workspace list before retrying"
            )
        else:
            try:
                if operation == "add":
                    return store.registry.add(name, Path(path)).model_dump()
                if operation == "remove":
                    entry = store.registry.read().get(name)
                    if entry is None:
                        raise ValueError("Unknown workspace")
                    store.registry.remove(name, entry.registration_id)
                    return {"workspace_id": name, "removed": True}
                return {
                    "workspaces": [
                        {**v.model_dump(), "state": "unloaded"}
                        for v in store.registry.read().values()
                    ]
                }
            finally:
                lock.release()


async def serve_management(store, run_id, pool):
    """Consume private run-bound requests; never expose registration over MCP."""
    while True:
        request = _read(store.control_path)
        response = _read(store.response_path)
        if request.get("run_id") == run_id and request.get(
            "request_id"
        ) != response.get("request_id"):
            response = {"request_id": request["request_id"]}
            try:
                operation = request["operation"]
                if operation == "add":
                    result = store.registry.add(
                        request["name"], Path(request["path"])
                    ).model_dump()
                elif operation == "remove":
                    await pool.remove(request["name"], force=request["force"])
                    result = {"workspace_id": request["name"], "removed": True}
                elif operation == "list":
                    result = {"workspaces": pool.list()}
                else:
                    raise ValueError("Unknown workspace command")
                response["result"] = result
            except Exception as exc:
                response["error"] = str(exc)
            write_json(store.response_path, response)
        await asyncio.sleep(0.05)
