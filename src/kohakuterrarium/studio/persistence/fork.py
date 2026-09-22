"""Session fork and branch handling.

Derives child paths, validates optional fork mutations, and drives
``SessionStore.fork`` without depending on HTTP schemas. Transport adapters
remain responsible for path resolution and response mapping.
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kohakuterrarium.errors import (
    ConflictError,
    InvalidRequestError,
    SessionError,
    SessionNotFoundError,
)
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.migrations import path_for_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store_open import open_owned_store
from kohakuterrarium.session.version import FORMAT_VERSION


def fork_target_path(parent: Path, fork_name: str) -> Path:
    """Return a versioned child path beside the parent session file."""
    base = parent.name.split(".kohakutr", 1)[0]
    child_bare = parent.parent / f"{base}-{fork_name}.kohakutr"
    return path_for_version(child_bare, FORMAT_VERSION)


def _drop_trailing(_evt: dict[str, Any]) -> None:
    return None


def _edit_user_message(content: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(evt: dict[str, Any]) -> dict[str, Any]:
        updated = dict(evt)
        updated["content"] = content
        return updated

    return mutate


def _inject_user_message(content: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(evt: dict[str, Any]) -> dict[str, Any]:
        updated = dict(evt)
        updated["_appended_user_message"] = content
        return updated

    return mutate


def _inject_tool_result(
    tool_call_id: str, output: str
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def mutate(evt: dict[str, Any]) -> dict[str, Any]:
        updated = dict(evt)
        injected = list(updated.get("_injected_tool_results") or [])
        injected.append({"call_id": tool_call_id, "output": output})
        updated["_injected_tool_results"] = injected
        return updated

    return mutate


def mutation_from_payload(
    kind: str,
    args: dict | None,
    fork_point_event: dict[str, Any],
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Validate a mutation request and return its event mutator.

    Mutations that replace user messages or inject tool results require a
    compatible fork-point event type.
    """
    args = args or {}
    fork_type = fork_point_event.get("type", "")

    if kind == "drop_trailing":
        return _drop_trailing

    if kind == "edit_user_message":
        if fork_type != "user_message":
            raise InvalidRequestError(
                "edit_user_message requires the fork-point event to be "
                f"a user_message, got {fork_type!r}"
            )
        content = args.get("content")
        if not isinstance(content, str):
            raise InvalidRequestError("edit_user_message requires args.content: str")
        return _edit_user_message(content)

    if kind == "inject_user_message":
        content = args.get("content")
        if not isinstance(content, str):
            raise InvalidRequestError("inject_user_message requires args.content: str")
        return _inject_user_message(content)

    if kind == "inject_tool_result":
        if fork_type != "assistant_tool_calls":
            raise InvalidRequestError(
                "inject_tool_result requires the fork-point event to be "
                f"an assistant_tool_calls, got {fork_type!r}"
            )
        tool_call_id = args.get("tool_call_id")
        output = args.get("output")
        if not isinstance(tool_call_id, str) or not isinstance(output, str):
            raise InvalidRequestError(
                "inject_tool_result requires args.tool_call_id: str "
                "and args.output: str"
            )
        return _inject_tool_result(tool_call_id, output)

    raise InvalidRequestError(f"Unknown mutate.kind: {kind}")


def find_fork_point(store: SessionStore, at_event_id: int) -> dict[str, Any] | None:
    """Return the event whose ``event_id == at_event_id``, or ``None``."""
    for _key, evt in store.get_all_events():
        if evt.get("event_id") == at_event_id:
            return evt
    return None


def _fork_into_child(
    store: SessionStore,
    target_path: str,
    *,
    at_event_id: int,
    mutate: Callable[[dict], dict | None] | None,
    name: str | None,
) -> tuple[str, str]:
    """Fork into a child store and close it on the calling thread.

    The child's complete SQLite lifecycle stays inside one dispatched call so
    creation, copy, and close all run on the parent store's affinity thread.
    """
    child = store.fork(
        target_path,
        at_event_id=at_event_id,
        mutate=mutate,
        name=name,
    )
    try:
        return child.session_id, child.path
    finally:
        child.close(update_status=False)


async def fork_session_handler(
    session_path: Path,
    *,
    at_event_id: int,
    mutate_kind: str | None,
    mutate_args: dict | None,
    name: str | None,
    store: SessionStore | None = None,
) -> dict[str, Any]:
    """Fork a resolved session path and return transport-neutral metadata.

    A supplied live store remains caller-owned and avoids reopening an actively
    written SQLite file. Missing paths are rejected before ``SessionStore`` can
    create them. Invalid mutations raise ``InvalidRequestError``; target or
    stability conflicts raise ``ConflictError``; other failures are wrapped in
    ``SessionError``.

    Every blocking store stage runs off the caller's event loop: the
    fork-point scan and the fork copy on the store's affinity thread, and the
    owned store's open/close on plain worker threads. The vault layer
    serializes access per call and is thread-agnostic, so handing the
    connection between those threads is safe — the stages are strictly
    sequenced, each ``await`` completing before the next starts.
    """
    session_path = Path(session_path)
    if not session_path.exists():
        raise SessionNotFoundError(f"Session not found: {session_path}")
    if at_event_id < 1:
        raise InvalidRequestError("at_event_id must be >= 1")

    owned = store is None
    try:
        if store is None:
            store = await open_owned_store(SessionStore, str(session_path))
        fork_point_event = await store.run(find_fork_point, store, at_event_id)
        if fork_point_event is None:
            raise InvalidRequestError(
                f"No event with event_id={at_event_id} in this session"
            )

        mutate: Callable[[dict], dict | None] | None = None
        if mutate_kind is not None:
            mutate = mutation_from_payload(mutate_kind, mutate_args, fork_point_event)

        fork_name = name or f"fork-{int(time.time())}"
        target_path = fork_target_path(session_path, fork_name)
        if target_path.exists():
            raise ConflictError(f"Fork target already exists: {target_path.name}")

        try:
            child_session_id, child_path = await store.run(
                _fork_into_child,
                store,
                str(target_path),
                at_event_id=at_event_id,
                mutate=mutate,
                name=name,
            )
        except ForkNotStableError as exc:
            raise ConflictError(str(exc)) from exc
        except (ValueError, FileExistsError) as exc:
            raise InvalidRequestError(str(exc)) from exc
    except (InvalidRequestError, ConflictError, SessionNotFoundError):
        raise
    except Exception as exc:
        raise SessionError(f"Fork failed: {exc}") from exc
    finally:
        if store is not None and owned:
            # Never dispatched through store.run: close shuts down the
            # affinity executor and cannot wait on itself.
            await asyncio.to_thread(store.close, update_status=False)

    return {
        "session_id": child_session_id,
        "fork_point": at_event_id,
        "path": child_path,
    }
