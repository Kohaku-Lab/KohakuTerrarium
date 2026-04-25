"""HTTP handlers for session fork / branch (Wave E).

Kept separate from ``sessions.py`` so the main route module stays
under the 600-line soft cap. The router in ``sessions.py`` delegates
to :func:`fork_session_handler` defined here.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.api.schemas import ForkMutationPayload, ForkRequest, ForkResponse
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.migrations import path_for_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import FORMAT_VERSION


def fork_target_path(parent: Path, fork_name: str) -> Path:
    """Derive the child ``.kohakutr`` path for a fork.

    Keeps the child in the same directory as the parent; the current
    :data:`FORMAT_VERSION` determines the file suffix via
    :func:`path_for_version`.
    """
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
    payload: ForkMutationPayload,
    fork_point_event: dict[str, Any],
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Map the HTTP mutation payload to a local mutator callable.

    Validates that the requested mutation is compatible with the
    fork-point event type. Raises ``HTTPException(400)`` on mismatch.
    """
    kind = payload.kind
    args = payload.args or {}
    fork_type = fork_point_event.get("type", "")

    if kind == "drop_trailing":
        return _drop_trailing

    if kind == "edit_user_message":
        if fork_type != "user_message":
            raise HTTPException(
                status_code=400,
                detail=(
                    "edit_user_message requires the fork-point event to be "
                    f"a user_message, got {fork_type!r}"
                ),
            )
        content = args.get("content")
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400,
                detail="edit_user_message requires args.content: str",
            )
        return _edit_user_message(content)

    if kind == "inject_user_message":
        content = args.get("content")
        if not isinstance(content, str):
            raise HTTPException(
                status_code=400,
                detail="inject_user_message requires args.content: str",
            )
        return _inject_user_message(content)

    if kind == "inject_tool_result":
        if fork_type != "assistant_tool_calls":
            raise HTTPException(
                status_code=400,
                detail=(
                    "inject_tool_result requires the fork-point event to be "
                    f"an assistant_tool_calls, got {fork_type!r}"
                ),
            )
        tool_call_id = args.get("tool_call_id")
        output = args.get("output")
        if not isinstance(tool_call_id, str) or not isinstance(output, str):
            raise HTTPException(
                status_code=400,
                detail=(
                    "inject_tool_result requires args.tool_call_id: str "
                    "and args.output: str"
                ),
            )
        return _inject_tool_result(tool_call_id, output)

    raise HTTPException(status_code=400, detail=f"Unknown mutate.kind: {kind}")


def find_fork_point(store: SessionStore, at_event_id: int) -> dict[str, Any] | None:
    """Return the event whose ``event_id == at_event_id``, or ``None``."""
    for _key, evt in store.get_all_events():
        if evt.get("event_id") == at_event_id:
            return evt
    return None


async def fork_session_handler(
    session_path: Path,
    payload: ForkRequest,
) -> ForkResponse:
    """Shared handler body for ``POST /sessions/{id}/fork``.

    Caller is responsible for resolving ``session_name`` to ``session_path``
    so this helper stays transport-agnostic.
    """
    if payload.at_event_id < 1:
        raise HTTPException(400, "at_event_id must be >= 1")

    store: SessionStore | None = None
    try:
        store = SessionStore(session_path)
        fork_point_event = find_fork_point(store, payload.at_event_id)
        if fork_point_event is None:
            raise HTTPException(
                400, f"No event with event_id={payload.at_event_id} in this session"
            )

        mutate: Callable[[dict], dict | None] | None = None
        if payload.mutate is not None:
            mutate = mutation_from_payload(payload.mutate, fork_point_event)

        fork_name = payload.name or f"fork-{int(time.time())}"
        target_path = fork_target_path(session_path, fork_name)
        if target_path.exists():
            raise HTTPException(409, f"Fork target already exists: {target_path.name}")

        try:
            child_store = store.fork(
                str(target_path),
                at_event_id=payload.at_event_id,
                mutate=mutate,
                name=payload.name,
            )
        except ForkNotStableError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (ValueError, FileExistsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        child_session_id = child_store.session_id
        child_path = child_store.path
        child_store.close(update_status=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fork failed: {exc}")
    finally:
        if store is not None:
            store.close(update_status=False)

    return ForkResponse(
        session_id=child_session_id,
        fork_point=payload.at_event_id,
        path=child_path,
    )
