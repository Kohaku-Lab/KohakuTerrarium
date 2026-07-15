"""Bridge a worker-local PTY session through Laboratory WebSocket frames."""

import asyncio
import json
from typing import Any

from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink, WSProxyAdapter
from kohakuterrarium.studio.attach.pty_router import pty_session, session_cwd
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class _FakeWebSocket:
    """Adapt a frame sink to the WebSocket subset required by ``pty_session``."""

    def __init__(self, sink: WSFrameSink) -> None:
        self._sink = sink

    async def send_json(self, frame: dict[str, Any]) -> None:
        await self._sink.send_json(frame)

    async def receive_text(self) -> str:
        # ``pty_session`` expects the raw JSON text returned by Starlette.
        frame = await self._sink.receive_json()
        return json.dumps(frame)

    async def close(self) -> None:
        # The proxy owns the underlying sink lifecycle.
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
        cwd = session_cwd(creature)

        fake_ws = _FakeWebSocket(sink)
        # Run the shell separately so stream setup can return before it exits.
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
