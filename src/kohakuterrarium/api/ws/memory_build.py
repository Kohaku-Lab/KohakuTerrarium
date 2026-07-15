"""WebSocket — stream memory-build progress for a saved session.

Mounted under the root prefix (no ``/api`` prefix) so the URL
matches the contract returned by ``POST /api/sessions/{name}/memory/build``:
``/ws/sessions/{name}/memory/build``.

Frames:

- ``{"phase": "scan"|"embed"|"write", "percent": int,
     "blocks_indexed": int, "blocks_total": int, "agent": str}``
- terminal: ``{"status": "ok"|"failed"|"cancelled",
              "error": str | None, "stats": dict | None}``
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.routes.persistence.memory_index import run_build_sync
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


_VALID_EMBEDDERS = {"auto", "model2vec", "sentence-transformer", "api"}

# Each session permits one build at a time because concurrent
# clear-and-reindex operations could leave its search index inconsistent.
_INFLIGHT_BUILDS: set[str] = set()
_INFLIGHT_LOCK = asyncio.Lock()


def _parse_query(ws: WebSocket) -> dict[str, Any]:
    """Parse optional build arguments from the WebSocket query string.

    Handshake parameters avoid a separate claim-ticket exchange after the
    HTTP endpoint returns the canonical build request.
    """
    q = dict(ws.query_params)
    embedder = q.get("embedder", "auto")
    if embedder not in _VALID_EMBEDDERS:
        embedder = "auto"
    model = q.get("model") or None
    dim_raw = q.get("dimensions")
    dimensions: int | None
    if dim_raw:
        try:
            dimensions = int(dim_raw)
        except ValueError:
            dimensions = None
    else:
        dimensions = None
    force = q.get("force", "false").lower() in ("1", "true", "yes")
    return {
        "embedder": embedder,
        "model": model,
        "dimensions": dimensions,
        "force": force,
    }


async def _stream_progress(
    ws: WebSocket, session_name: str, args: dict[str, Any]
) -> None:
    """Run a memory build in a worker thread and stream its progress frames."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)

    def progress(frame: dict[str, Any]) -> None:
        """Transfer worker-thread progress without blocking the build."""
        # Slow consumers lose intermediate progress rather than stalling the
        # indexing thread.
        try:
            loop.call_soon_threadsafe(_queue_put_nowait, queue, frame)
        except RuntimeError:
            # A disconnected client may close the loop while the worker finishes.
            pass

    async def run_build() -> dict[str, Any]:
        """Execute the synchronous indexer outside the event loop."""
        return await asyncio.to_thread(
            run_build_sync,
            session_name,
            embedder=args["embedder"],
            model=args["model"],
            dimensions=args["dimensions"],
            force=args["force"],
            progress=progress,
        )

    build_task = asyncio.create_task(run_build())
    sender_done = asyncio.Event()

    async def sender() -> None:
        """Forward queued progress frames until the sentinel arrives."""
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    return
                try:
                    await ws.send_text(json.dumps(frame))
                except WebSocketDisconnect:
                    return
        finally:
            sender_done.set()

    sender_task = asyncio.create_task(sender())

    try:
        result = await build_task
        # Yield once so callbacks already scheduled on the loop reach the queue.
        await asyncio.sleep(0)
        terminal = {
            "status": "ok",
            "error": None,
            "stats": result.get("stats") or {},
            "indexed_per_agent": result.get("indexed_per_agent") or {},
        }
    except asyncio.CancelledError:
        terminal = {"status": "cancelled", "error": None, "stats": None}
        raise
    except LookupError as e:
        terminal = {"status": "failed", "error": str(e), "stats": None}
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("memory build failed")
        terminal = {"status": "failed", "error": str(e), "stats": None}
    finally:
        # The sentinel lets the sender finish after all queued progress frames.
        try:
            await queue.put(None)
        except Exception:
            pass
        try:
            await asyncio.wait_for(sender_done.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            sender_task.cancel()

    try:
        await ws.send_text(json.dumps(terminal))
    except WebSocketDisconnect:
        return


def _queue_put_nowait(queue: asyncio.Queue, frame: dict[str, Any] | None) -> None:
    """Queue progress without blocking, preferring the newest frame."""
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:
        # The newest progress state is more useful than a stale intermediate one.
        try:
            _ = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            pass


@router.websocket("/ws/sessions/{session_name}/memory/build")
async def ws_memory_build(ws: WebSocket, session_name: str) -> None:
    """Stream one guarded memory-index build for a session."""
    await accept_with_auth_echo(ws)
    # Overlapping clear-and-reindex operations could produce inconsistent FTS rows.
    async with _INFLIGHT_LOCK:
        already = session_name in _INFLIGHT_BUILDS
        if not already:
            _INFLIGHT_BUILDS.add(session_name)
    if already:
        try:
            await ws.send_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error": (
                            "another build is already running for this session; "
                            "wait for it to finish or cancel it first"
                        ),
                        "stats": None,
                    }
                )
            )
        finally:
            try:
                await ws.close()
            except Exception:  # pragma: no cover - already closed
                pass
        return

    args = _parse_query(ws)
    try:
        await _stream_progress(ws, session_name, args)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
    finally:
        async with _INFLIGHT_LOCK:
            _INFLIGHT_BUILDS.discard(session_name)
        try:
            await ws.close()
        except Exception:  # pragma: no cover - already closed
            pass


__all__ = ["router"]
