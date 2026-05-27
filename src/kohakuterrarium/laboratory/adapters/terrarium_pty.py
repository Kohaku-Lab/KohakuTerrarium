"""APP extension adapter for ``terrarium.pty`` — PTY shell over Lab.

Subclass of :class:`WSProxyAdapter` (the unified ws-forwarder).
Spawns a PTY shell in the creature's working directory ON THE
WORKER and bridges stdin / stdout / resize frames bidirectionally to
the controller's frontend WebSocket through the lab transport.

How the lift works without duplicating ``pty_posix`` / ``pty_windows``:

- The existing ``studio.attach.pty_router.pty_session(websocket, cwd)``
  takes a :class:`fastapi.WebSocket` directly.  This adapter wraps the
  :class:`WSFrameSink` from the proxy base class in a
  :class:`_FakeWebSocket` that satisfies the same interface
  (``send_json`` / ``receive_text``).  ``pty_session`` runs unchanged.
- ``send_json`` writes onto the sink's outbox (controller → frontend
  WS path).
- ``receive_text`` awaits ``sink.receive_json()`` and JSON-encodes it
  back to the string that ``pty_session`` expects.

Backpressure: the sink's bounded outbox protects against PTY output
bursts (``ls /tmp`` on a populated tree).  Frames are coalesced at the
transport layer.

Process lifecycle: ``on_close`` cancels the PTY task; ``pty_session``
already kills the child process when its WebSocket is gone.
"""

import asyncio
import json
from typing import Any

from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink, WSProxyAdapter
from kohakuterrarium.studio.attach.pty_router import _session_cwd, pty_session
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class _FakeWebSocket:
    """WebSocket lookalike that bridges to a :class:`WSFrameSink`.

    Exposes the subset of ``fastapi.WebSocket`` that
    ``studio.attach.pty_*`` actually calls: ``send_json``,
    ``receive_text``, plus a ``close`` no-op so disconnect paths don't
    crash.  Errors from the underlying sink surface as ``RuntimeError``
    — ``pty_session`` already handles ``WebSocketDisconnect`` so we
    raise that type too.
    """

    def __init__(self, sink: WSFrameSink) -> None:
        self._sink = sink

    async def send_json(self, frame: dict[str, Any]) -> None:
        await self._sink.send_json(frame)

    async def receive_text(self) -> str:
        # PTY's write loop reads JSON-strings rather than JSON-objects
        # because ``starlette``'s ``receive_text`` returns the raw
        # client message body.  We re-serialise the sink frame so the
        # unmodified ``json.loads`` in the producer path works.
        frame = await self._sink.receive_json()
        return json.dumps(frame)

    async def close(self) -> None:
        # Sink lifecycle is owned by the proxy base class; nothing
        # for the fake-WS itself to do.
        return None


class TerrariumPtyAdapter(WSProxyAdapter):
    """Worker-side ``terrarium.pty`` APP extension."""

    NAMESPACE = "terrarium.pty"

    def __init__(self, engine: Terrarium, lab_node: LabRegistrar) -> None:
        self._engine = engine
        super().__init__(lab_node)

    async def on_start(
        self,
        body: dict[str, Any],
        sink: WSFrameSink,
    ) -> dict[str, Any] | None:
        creature_id = body["creature_id"]
        creature = self._engine.get_creature(creature_id)
        cwd = _session_cwd(creature)

        fake_ws = _FakeWebSocket(sink)
        # ``pty_session`` is long-running until the shell exits or the
        # WS closes — spawn as a task so on_start can return
        # immediately with setup info for the controller.
        task = asyncio.create_task(self._run_pty(fake_ws, cwd, sink))
        self._sessions[sink.stream_id] = {"task": task, "cwd": cwd}
        return {"setup": {"type": "ready", "cwd": cwd}}

    async def on_close(self, stream_id: str) -> None:
        session = self._sessions.get(stream_id)
        if session is None:
            return
        task = session.get("task")
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run_pty(
        self, fake_ws: _FakeWebSocket, cwd: str, sink: WSFrameSink
    ) -> None:
        try:
            await pty_session(fake_ws, cwd)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("pty session ended", error=str(exc), exc_info=True)
            sink.send_json_nowait({"type": "error", "data": str(exc)})


__all__ = ["TerrariumPtyAdapter"]
