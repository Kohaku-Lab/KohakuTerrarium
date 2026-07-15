"""Persistence memory-index — status + build for a saved session's
vector index. The CLI equivalent is ``kt embedding <session>``.

Mounting under ``/api/sessions`` preserves the existing
``/api/sessions/{name}/memory/search`` URL family.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.routes.persistence._executor import (
    run_in_persistence_executor,
)
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.sessions.memory_build import (
    build_index as _build_index,
    index_status as _index_status,
)

router = APIRouter()


class BuildIndexRequest(BaseModel):
    embedder: Literal["model2vec", "sentence-transformer", "api", "auto"] = "auto"
    model: str | None = None
    dimensions: int | None = None
    force: bool = False


class MemoryStatus(BaseModel):
    indexed: bool
    embedder: str | None
    model: str | None
    dimensions: int | None
    fts_blocks: int
    vec_blocks: int
    agents: list[str]


@router.get("/{session_name}/memory/status", response_model=MemoryStatus)
async def get_memory_status(session_name: str) -> MemoryStatus:
    """Return the current vector-index state for a saved session."""
    path = await run_in_persistence_executor(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    payload = await run_in_persistence_executor(_index_status, path)
    return MemoryStatus(**payload)


@router.post("/{session_name}/memory/build")
async def post_memory_build(
    session_name: str, body: BuildIndexRequest
) -> dict[str, Any]:
    """Validate a build request and return its progress WebSocket path.

    The WebSocket performs the build and receives request options through its
    query string, avoiding a separate ticket or claim handshake.
    """
    path = await run_in_persistence_executor(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return {
        "websocket": f"/ws/sessions/{session_name}/memory/build",
        "request": body.model_dump(),
    }


def run_build_sync(
    session_name: str,
    *,
    embedder: str,
    model: str | None,
    dimensions: int | None,
    force: bool,
    progress,
) -> dict[str, Any]:
    """Resolve a saved session and build its index synchronously.

    Exceptions propagate so the WebSocket handler can emit its terminal failure
    frame. The synchronous boundary allows the complete build to run in a
    worker thread.
    """
    path = resolve_session_path_default(session_name)
    if path is None:
        raise LookupError(f"Session not found: {session_name}")
    return _build_index(
        path,
        provider=embedder,
        model=model,
        dimensions=dimensions,
        force=force,
        progress=progress,
    )


__all__ = ["router", "run_build_sync"]
