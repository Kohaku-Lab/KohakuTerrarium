"""Run-bound local commands, execution acknowledgements and management health."""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kohakuterrarium.mcp_server.records import lifecycle_command, write_json
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy

logger = logging.getLogger(__name__)


def reset_management(store):
    """Reset the mailbox before admitting a new run, under its instance lock."""
    write_json(store.control_path, {})
    write_json(store.response_path, {})


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    operation: Literal["add", "remove", "list"]
    name: str | None = None
    path: str | None = None
    force: bool = False

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation != "list" and not self.name:
            raise ValueError("Workspace name is required")
        if self.operation == "add" and not self.path:
            raise ValueError("Workspace path is required")
        return self


def _read(path):
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Management record must be an object")
    return data


def stop_requested(store, run_id):
    try:
        return _read(store.stop_path).get("run_id") == run_id
    except (ValueError, OSError):
        return False


def _matches(response, request):
    return (
        bool(request.get("request_id"))
        and response.get("run_id") == request.get("run_id")
        and response.get("request_id") == request["request_id"]
        and (
            (isinstance(response.get("result"), dict) and "error" not in response)
            or (isinstance(response.get("error"), str) and "result" not in response)
        )
    )


def _read_response(store, run_id):
    response = _read(store.response_path)
    if (
        response
        and response.get("run_id") == run_id
        and (
            not isinstance(response.get("request_id"), str)
            or not _matches(response, response)
        )
    ):
        raise ValueError("Invalid management acknowledgement")
    return response


def _snapshot(store, *, running, error=None):
    runtime = store.runtime()
    state = dict(runtime.get("management") or {})
    try:
        request = _read(store.control_path)
        response = _read_response(store, runtime.get("run_id"))
        if request.get("run_id") == runtime.get("run_id") and request:
            state.update(
                request_id=request.get("request_id"),
                outcome="confirmed" if _matches(response, request) else "unconfirmed",
            )
    except (ValueError, OSError):
        state.update(outcome="unconfirmed")
    if error:
        state["error"] = error
    if not running:
        state["state"] = "stopped"
    return {
        "source": "registry_snapshot",
        "management": state,
        "workspaces": [
            {**entry.model_dump(), "state": "unknown" if running else "unloaded"}
            for entry in store.registry.read().values()
        ],
    }


def manage(store, operation, *, name=None, path=None, force=False, wait=30):
    """Apply a command; unconfirmed outcomes never authorize automatic replay.

    A list blocked by management returns a labelled persistent registry snapshot,
    without overwriting the command slot or claiming live runtime state.
    """
    request = _Request(
        run_id="offline",
        request_id=uuid.uuid4().hex,
        operation=operation,
        name=name,
        path=str(Path(path).expanduser().resolve()) if path else None,
        force=force,
    )
    if operation == "list":
        lock = FileLock(store.command_lock.path)
        try:
            lock.acquire()
        except FileLockBusy:
            return _snapshot(store, running=True, error="Lifecycle command in progress")
        try:
            return _manage_locked(store, request, wait)
        finally:
            lock.release()
    with lifecycle_command(FileLock(store.command_lock.path)):
        return _manage_locked(store, request, wait)


def _manage_locked(store, request, wait):
    lock = FileLock(store.instance_lock.path)
    try:
        lock.acquire()
    except FileLockBusy:
        try:
            return _send(store, request, wait)
        except (RuntimeError, ValueError, OSError) as exc:
            if request.operation == "list":
                return _snapshot(store, running=True, error=str(exc))
            raise
    else:
        try:
            if request.operation == "add":
                return store.registry.add(request.name, Path(request.path)).model_dump()
            if request.operation == "remove":
                entry = store.registry.read().get(request.name)
                if entry is None:
                    raise ValueError("Unknown workspace")
                store.registry.remove(request.name, entry.registration_id)
                return {"workspace_id": request.name, "removed": True}
            return _snapshot(store, running=False)
        finally:
            lock.release()


def _send(store, request, wait):
    runtime = store.runtime()
    run_id = runtime.get("run_id")
    if not run_id:
        raise RuntimeError("Endpoint ownership unavailable; no command submitted")
    if stop_requested(store, run_id):
        raise RuntimeError("Endpoint is stopping; no command submitted")
    health = runtime.get("management") or {}
    if health.get("protocol_version") != 1 or health.get("state") == "failed":
        raise RuntimeError(
            "Management unavailable; restart the endpoint before submitting commands"
        )
    if health.get("state") == "stopping":
        raise RuntimeError("Endpoint is stopping; no command submitted")
    try:
        previous, response = _read(store.control_path), _read_response(store, run_id)
    except (ValueError, OSError):
        raise RuntimeError(
            "Management records unavailable; inspect workspace list or restart; no command submitted"
        ) from None
    if previous.get("run_id") == run_id and not _matches(response, previous):
        raise RuntimeError(
            "A workspace command is still pending; inspect workspace list before retrying"
        )
    request.run_id = run_id
    data = request.model_dump()
    write_json(store.control_path, data)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            response = _read_response(store, run_id)
        except (ValueError, OSError):
            response = {}
        if _matches(response, data):
            if "error" in response:
                raise ValueError(response["error"])
            return response["result"]
        current = store.runtime()
        if current.get("run_id") != run_id or (current.get("management") or {}).get(
            "state"
        ) in {"failed", "stopping"}:
            break
        time.sleep(0.05)
    raise RuntimeError(
        "Workspace command outcome is unconfirmed; inspect workspace list before retrying; never replay automatically"
    )


def management_snapshot(task, health):
    """Report an unexpected consumer exit without stopping HTTP or its jobs."""
    if task.done() and health.get("state") != "stopping":
        if health.get("state") != "failed":
            error = None if task.cancelled() else task.exception()
            logger.error("MCP management consumer exited: %s", type(error).__name__)
        health.update(
            state="failed",
            error="Management consumer exited; restart the endpoint. Pending command outcomes are unconfirmed.",
        )
    return dict(health)


async def serve_management(store, run_id, pool, *, health=None):
    """Consume serial local requests; retain completed replies until published."""
    health = health if health is not None else {}
    health.update(protocol_version=1, state="ready", error=None)
    completed = None
    unpublished = False
    while True:
        if stop_requested(store, run_id):
            health.update(state="stopping")
            return
        try:
            if unpublished:
                await asyncio.to_thread(write_json, store.response_path, completed)
                unpublished = False
            request = _read(store.control_path)
            if request.get("run_id") != run_id:
                health.update(state="ready", error=None)
            elif (
                not isinstance(request.get("request_id"), str)
                or not request["request_id"]
            ):
                raise ValueError("Invalid management request identity")
            else:
                health.update(request_id=request["request_id"])
                if completed is not None and _matches(completed, request):
                    try:
                        published = _matches(_read_response(store, run_id), request)
                    except (OSError, ValueError):
                        published = False
                    if not published:
                        unpublished = True
                        continue
                elif not _matches(_read_response(store, run_id), request):
                    health.update(state="busy", outcome="unconfirmed", error=None)
                    completed = {"run_id": run_id, "request_id": request["request_id"]}
                    try:
                        command = _Request.model_validate(request)
                    except ValueError:
                        completed["error"] = (
                            "Invalid management operation or arguments; not executed"
                        )
                    else:
                        try:
                            if command.operation == "add":
                                result = store.registry.add(
                                    command.name, Path(command.path)
                                ).model_dump()
                            elif command.operation == "remove":
                                await pool.remove(command.name, force=command.force)
                                result = {"workspace_id": command.name, "removed": True}
                            else:
                                result = {"workspaces": pool.list()}
                            completed["result"] = result
                        except Exception as exc:
                            completed["error"] = str(exc)
                    unpublished = True
                    continue
                health.update(state="ready", outcome="confirmed", error=None)
        except (OSError, ValueError):
            health.update(
                state="degraded",
                error="Management record unreadable or acknowledgement unavailable; inspect workspace list. Unconfirmed commands must not be replayed.",
            )
        await asyncio.sleep(0.05)
