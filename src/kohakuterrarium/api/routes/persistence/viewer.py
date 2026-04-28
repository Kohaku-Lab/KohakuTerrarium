"""Persistence viewer — tree / summary / turns / events / diff / export.

Read-only endpoints for the Session Viewer (V1+V6 waves). Paths are
``/{session_name}/<noun>`` so the router can be mounted under
``/api/sessions`` for URL preservation.

All handlers open the store read-only (``close(update_status=False)``)
so browsing never bumps ``last_active``.
"""

from typing import Any

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


def _open_or_404(session_name: str) -> tuple[SessionStore, str]:
    path = resolve_session_path_default(session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return SessionStore(path), normalize_session_stem(path)


@router.get("/{session_name}/tree")
async def get_session_tree(session_name: str) -> dict[str, Any]:
    store, canonical = _open_or_404(session_name)
    try:
        return build_tree_payload(store, canonical)
    finally:
        store.close(update_status=False)


@router.get("/{session_name}/summary")
async def get_session_summary(
    session_name: str, agent: str | None = None
) -> dict[str, Any]:
    store, canonical = _open_or_404(session_name)
    try:
        return build_summary_payload(store, canonical, agent)
    finally:
        store.close(update_status=False)


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
    store, canonical = _open_or_404(session_name)
    try:
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
    finally:
        store.close(update_status=False)


@router.get("/{session_name}/export")
async def get_session_export(
    session_name: str,
    format: str = "md",
    agent: str | None = None,
) -> Response:
    """Stream a session transcript in ``md`` / ``html`` / ``jsonl``."""
    path = resolve_session_path_default(session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    store = SessionStore(path)
    try:
        content_type, body = build_export(
            store, normalize_session_stem(path), format.lower(), agent
        )
    finally:
        store.close(update_status=False)
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
    a_path = resolve_session_path_default(session_name)
    if a_path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    b_path = resolve_session_path_default(other)
    if b_path is None:
        raise HTTPException(404, f"Other session not found: {other}")
    return build_diff_payload(a_path, b_path, agent=agent)


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
    store, canonical = _open_or_404(session_name)
    try:
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
    finally:
        store.close(update_status=False)
