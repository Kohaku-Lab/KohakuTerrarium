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

from kohakuterrarium.api.routes.persistence.memory_index import run_build_sync
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


_VALID_EMBEDDERS = {"auto", "model2vec", "sentence-transformer", "api"}

# Module-level guard: at most one in-flight build per session_name. A
# concurrent build on the same session is rejected at WS-accept time
# with a terminal "failed" frame so the second client gets a clean
# error instead of racing the first build through SessionMemory's
# clear-and-reindex path (BUG: integrity audit Phase K).
_INFLIGHT_BUILDS: set[str] = set()
_INFLIGHT_LOCK = asyncio.Lock()


def _parse_query(ws: WebSocket) -> dict[str, Any]:
    """Pull build args off the WS query string; tolerate omission.

    The HTTP ``POST`` returns the canonical body — but URL handshake
    is the simplest way to attach params to a WS without a separate
    claim-ticket round-trip. Mirrors the app-update pattern.
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
    """Run the build on a thread; relay frames over the WS.

    Producer (the build's progress callback) drops dicts into a
    bounded queue; consumer awaits them and ``ws.send_text``s. When
    the worker thread finishes (success, failure, or cancellation),
    we send the terminal frame and return.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)

    def progress(frame: dict[str, Any]) -> None:
        # Called from the worker thread — bounce the dict onto the
        # event loop. ``call_soon_threadsafe`` + ``put_nowait`` keeps
        # the producer non-blocking; if the consumer is slow we drop
        # the frame rather than stalling the build.
        try:
            loop.call_soon_threadsafe(_queue_put_nowait, queue, frame)
        except RuntimeError:
            # Loop closed mid-build (client disconnected); the worker
            # thread will finish naturally.
            pass

    async def run_build() -> dict[str, Any]:
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
        # Drain any in-flight progress frames before terminal.
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
        # Stop the sender — sentinel ``None`` ends its loop cleanly.
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
    """Drop the frame if the queue is full — back-pressure, not stall."""
    try:
        queue.put_nowait(frame)
    except asyncio.QueueFull:
        # Drop oldest, then push the new frame so the latest progress
        # always wins — a stale 30% frame is less useful than the
        # current 65%.
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
    await ws.accept()
    # Reject overlapping builds for the same session — they'd race
    # SessionMemory's clear-and-reindex path and produce torn FTS rows.
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
