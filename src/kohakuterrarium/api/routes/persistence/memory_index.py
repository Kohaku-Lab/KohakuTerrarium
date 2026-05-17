"""Persistence memory-index — status + build for a saved session's
vector index. CLI equivalent: ``kt embedding <session>``.

Mounted under ``/api/sessions`` (URL preservation matching the
existing ``/api/sessions/{name}/memory/search`` shape).
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
    """Snapshot the vector-index state for a saved session."""
    path = await run_in_persistence_executor(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    payload = await run_in_persistence_executor(_index_status, path)
    return MemoryStatus(**payload)


@router.post("/{session_name}/memory/build")
async def post_memory_build(
    session_name: str, body: BuildIndexRequest
) -> dict[str, Any]:
    """Acknowledge a build request and return the WS path for progress.

    The actual run happens on the WebSocket at
    ``/ws/sessions/{name}/memory/build`` — the build request body
    is encoded as the URL's query string so the WS handler can pick
    it up without a separate "claim ticket" handshake. Same pattern
    the app-update flow uses.
    """
    path = await run_in_persistence_executor(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return {
        "websocket": f"/ws/sessions/{session_name}/memory/build",
        "request": body.model_dump(),
    }


# Synchronous helper exposed for the WS handler so it can run the
# build on a worker thread without re-importing this module's body.
def run_build_sync(
    session_name: str,
    *,
    embedder: str,
    model: str | None,
    dimensions: int | None,
    force: bool,
    progress,
) -> dict[str, Any]:
    """Sync entry — resolve the session path then invoke ``build_index``.

    Raised exceptions propagate to the caller; the WS handler turns
    them into a ``{"status": "failed", "error": ...}`` terminal frame.
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
