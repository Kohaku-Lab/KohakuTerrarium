"""Persistence viewer — tree / summary / turns / events / diff / export.

Read-only endpoints for the Session Viewer (V1+V6 waves). Paths are
``/{session_name}/<noun>`` so the router can be mounted under
``/api/sessions`` for URL preservation.

All handlers open the store read-only (``close(update_status=False)``)
so browsing never bumps ``last_active``. Every payload builder is
sync (SQLite + filesystem), so each route dispatches the open +
build + close sequence to a worker thread via ``asyncio.to_thread`` —
the event loop stays free for concurrent API traffic.
"""

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.persistence.viewer.diff import build_diff_payload
from kohakuterrarium.studio.persistence.viewer.events import build_events_payload
from kohakuterrarium.studio.persistence.viewer.export import build_export
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload
from kohakuterrarium.studio.persistence.viewer.tree import build_tree_payload
from kohakuterrarium.studio.persistence.viewer.turns import build_turns_payload

router = APIRouter()


async def _resolve_or_404(session_name: str):
    """Resolve a session path off-loop; raise 404 if missing."""
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path


def _run_with_store(path, builder: Callable[[SessionStore, str], Any]) -> Any:
    """Open store, run builder, close — all on the calling thread.

    Designed to be wrapped in :func:`asyncio.to_thread` so the SQLite
    open + the payload build + the close happen as one off-loop unit.
    """
    store = SessionStore(path)
    try:
        return builder(store, normalize_session_stem(path))
    finally:
        store.close(update_status=False)


@router.get("/{session_name}/tree")
async def get_session_tree(session_name: str) -> dict[str, Any]:
    path = await _resolve_or_404(session_name)
    return await asyncio.to_thread(_run_with_store, path, build_tree_payload)


@router.get("/{session_name}/summary")
async def get_session_summary(
    session_name: str, agent: str | None = None
) -> dict[str, Any]:
    path = await _resolve_or_404(session_name)

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_summary_payload(store, canonical, agent)

    return await asyncio.to_thread(_run_with_store, path, _build)


@router.get("/{session_name}/turns")
async def get_session_turns(
    session_name: str,
    agent: str | None = None,
    from_turn: int | None = None,
    to_turn: int | None = None,
    limit: int = 200,
    offset: int = 0,
    aggregate: bool = False,
) -> dict[str, Any]:
    path = await _resolve_or_404(session_name)

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_turns_payload(
            store,
            canonical,
            agent=agent,
            from_turn=from_turn,
            to_turn=to_turn,
            limit=max(1, min(limit, 1000)),
            offset=max(0, offset),
            aggregate=aggregate,
        )

    return await asyncio.to_thread(_run_with_store, path, _build)


@router.get("/{session_name}/export")
async def get_session_export(
    session_name: str,
    format: str = "md",
    agent: str | None = None,
) -> Response:
    """Stream a session transcript in ``md`` / ``html`` / ``jsonl``."""
    path = await _resolve_or_404(session_name)

    def _build(store: SessionStore, canonical: str) -> tuple[str, bytes | str]:
        return build_export(store, canonical, format.lower(), agent)

    content_type, body = await asyncio.to_thread(_run_with_store, path, _build)
    ext = "md" if format == "md" else format.lower()
    filename = f"{normalize_session_stem(path)}.{ext}"
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_name}/diff")
async def get_session_diff(
    session_name: str,
    other: str,
    agent: str | None = None,
) -> dict[str, Any]:
    """Structured diff against another saved session."""
    a_path = await _resolve_or_404(session_name)
    b_path = await asyncio.to_thread(resolve_session_path_default, other)
    if b_path is None:
        raise HTTPException(404, f"Other session not found: {other}")
    return await asyncio.to_thread(build_diff_payload, a_path, b_path, agent=agent)


@router.get("/{session_name}/events")
async def get_session_events(
    session_name: str,
    agent: str | None = None,
    turn_index: int | None = None,
    types: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = 200,
    cursor: int | None = None,
) -> dict[str, Any]:
    path = await _resolve_or_404(session_name)

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_events_payload(
            store,
            canonical,
            agent=agent,
            turn_index=turn_index,
            types=types,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=max(1, min(limit, 1000)),
            cursor=cursor,
        )

    return await asyncio.to_thread(_run_with_store, path, _build)
