"""APP extension adapter for ``terrarium.attach`` — WS-frame proxy.

Subclass of :class:`WSProxyAdapter` (the unified ws-forwarder in
``laboratory/ws_proxy.py``).  Mirrors the host-side
``studio.attach.io.attach_io`` behaviour over the Lab transport: the
controller's frontend WebSocket gets the FULL event stream from a
remote creature — tokens, tool calls, sub-agent events, channel
messages, processing markers, interactive UI events.

Lifecycle for a single attach session (``stream_id``):

1. Controller opens the lab stream via ``terrarium.attach.start``
   with body ``{creature_id, session_id}``.  This adapter resolves
   the creature, attaches a :class:`StreamOutput` to its
   ``output_router``, subscribes siblings in the same graph, and
   registers shared-channel callbacks.  All four sinks pump frames
   into the same ``WSFrameSink``.  Returns the initial
   ``session_info`` frame under the ``setup`` key so the controller
   forwards it BEFORE the first streamed frame.
2. Controller forwards every WS frame to
   ``terrarium.attach.input``; a consumer task on this side awaits
   ``sink.receive_json()`` and dispatches by ``frame.type``:
   ``input`` → fire-and-forget ``agent.inject_input``, ``ui_reply`` →
   ``output_router.submit_reply_with_status`` + echo ``ui_reply_ack``,
   ``ui_dismiss`` → noop.
3. ``terrarium.attach.cancel`` (or RemoteStream.aclose) tears down
   every sink, removes channel callbacks, and stops the consumer.
"""

import asyncio
import time
from typing import Any

from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink, WSProxyAdapter
from kohakuterrarium.llm.message import (
    content_parts_to_dicts,
    normalize_content_parts,
)
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.studio.attach._event_stream import StreamOutput, get_event_log
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class _AttachSession:
    """Per-stream bookkeeping for an attach session."""

    def __init__(
        self,
        creature: Any,
        agent: Any,
        primary_out: StreamOutput,
        sibling_modules: list[tuple[Any, Any]],
        channel_cbs: list[tuple[Any, Any]],
        consumer_task: asyncio.Task,
    ) -> None:
        self.creature = creature
        self.agent = agent
        self.primary_out = primary_out
        self.sibling_modules = sibling_modules
        self.channel_cbs = channel_cbs
        self.consumer_task = consumer_task
        self.input_tasks: list[asyncio.Task] = []

    def teardown(self) -> None:
        try:
            self.agent.output_router.remove_secondary(self.primary_out)
        except Exception:
            logger.debug("failed to remove primary sink", exc_info=True)
        for sib_agent, sib_module in self.sibling_modules:
            try:
                sib_agent.output_router.remove_secondary(sib_module)
            except Exception:
                logger.debug("failed to remove sibling sink", exc_info=True)
        for ch, cb in self.channel_cbs:
            try:
                ch.remove_on_send(cb)
            except Exception:
                logger.debug("failed to remove channel callback", exc_info=True)
        if not self.consumer_task.done():
            self.consumer_task.cancel()


class _SinkQueueAdapter:
    """Shim that makes a :class:`WSFrameSink` look like an ``asyncio.Queue``.

    :class:`StreamOutput` only calls ``put_nowait`` on its queue; the
    sink's sync ``send_json_nowait`` matches that interface exactly so
    we can drop the sink in as the queue without touching
    :class:`StreamOutput`.
    """

    def __init__(self, sink: WSFrameSink) -> None:
        self._sink = sink

    def put_nowait(self, frame: dict) -> None:
        self._sink.send_json_nowait(frame)


class TerrariumAttachAdapter(WSProxyAdapter):
    """Worker-side ``terrarium.attach`` APP extension."""

    NAMESPACE = "terrarium.attach"

    def __init__(self, engine: Terrarium, lab_node: LabRegistrar) -> None:
        self._engine = engine
        super().__init__(lab_node)

    async def on_start(
        self,
        body: dict[str, Any],
        sink: WSFrameSink,
    ) -> dict[str, Any] | None:
        creature_id = body["creature_id"]
        session_id = body.get("session_id", "_")
        creature = self._engine.get_creature(creature_id)
        agent = creature.agent

        log = get_event_log(f"{session_id}:{creature.creature_id}")
        queue_shim = _SinkQueueAdapter(sink)
        primary = StreamOutput(creature.name, queue_shim, log)  # type: ignore[arg-type]
        agent.output_router.add_secondary(primary)

        # Sibling subscribe — mirrors the host attach for terrarium graphs.
        sibling_modules: list[tuple[Any, Any]] = []
        if creature.graph_id and creature.graph_id in self._engine._topology.graphs:
            graph = self._engine._topology.graphs[creature.graph_id]
            for cid in graph.creature_ids:
                if cid == creature.creature_id:
                    continue
                try:
                    sibling = self._engine.get_creature(cid)
                except KeyError:
                    continue
                sib_module = StreamOutput(sibling.name, queue_shim, log)  # type: ignore[arg-type]
                sibling.agent.output_router.add_secondary(sib_module)
                sibling_modules.append((sibling.agent, sib_module))

        # Channel callbacks + history replay.
        channel_cbs = self._register_channel_callbacks(creature.graph_id, sink)
        self._replay_channel_history(creature.graph_id, sink)

        consumer_task = asyncio.create_task(self._consume_input(sink, creature, agent))
        self._sessions[sink.stream_id] = _AttachSession(
            creature=creature,
            agent=agent,
            primary_out=primary,
            sibling_modules=sibling_modules,
            channel_cbs=channel_cbs,
            consumer_task=consumer_task,
        )

        session_info = {
            "type": "activity",
            "activity_type": "session_info",
            "source": creature.name,
            "model": agent.config.model,
            "agent_name": creature.name,
            "ts": time.time(),
        }
        return {"setup": session_info}

    async def on_close(self, stream_id: str) -> None:
        session = self._sessions.get(stream_id)
        if session is not None:
            session.teardown()

    # ------------------------------------------------------------------
    # Consumer — pulls inbound frames from the sink and dispatches.
    # ------------------------------------------------------------------

    async def _consume_input(
        self,
        sink: WSFrameSink,
        creature: Any,
        agent: Any,
    ) -> None:
        try:
            while True:
                frame = await sink.receive_json()
                frame_type = frame.get("type")
                if frame_type == "ui_reply":
                    self._handle_ui_reply(sink, agent, creature.name, frame)
                    continue
                if frame_type == "ui_dismiss":
                    continue
                if frame_type != "input":
                    continue
                content = _normalize_input_content(frame)
                if not content:
                    continue
                target_name = (frame.get("target") or "").strip()
                target_agent = agent
                target_name_eff = creature.name
                if target_name and target_name != creature.name:
                    sibling = self._find_sibling_by_name(creature, target_name)
                    if sibling is None:
                        sink.send_json_nowait(
                            {
                                "type": "error",
                                "source": target_name,
                                "content": (
                                    f"Cannot route to creature {target_name!r}: "
                                    "not found in this session."
                                ),
                                "ts": time.time(),
                            }
                        )
                        continue
                    target_agent = sibling.agent
                    target_name_eff = sibling.name
                sink.send_json_nowait(
                    {
                        "type": "user_input",
                        "source": target_name_eff,
                        "content": content,
                        "ts": time.time(),
                    }
                )
                asyncio.create_task(
                    self._process_input(sink, target_agent, content, target_name_eff)
                )
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_channel_callbacks(
        self, graph_id: str, sink: WSFrameSink
    ) -> list[tuple[Any, Any]]:
        env = self._engine._environments.get(graph_id)
        if env is None or not env.shared_channels.list_channels():
            return []
        cbs: list[tuple[Any, Any]] = []

        def make_cb(ch_name: str):
            def cb(channel_name, message):
                ts = (
                    message.timestamp.isoformat()
                    if hasattr(message.timestamp, "isoformat")
                    else str(message.timestamp)
                )
                sink.send_json_nowait(
                    {
                        "type": "channel_message",
                        "source": "channel",
                        "channel": channel_name,
                        "sender": message.sender,
                        "content": message.content,
                        "message_id": message.message_id,
                        "timestamp": ts,
                        "ts": time.time(),
                    }
                )

            return cb

        for ch in env.shared_channels._channels.values():
            cb = make_cb(ch.name)
            ch.on_send(cb)
            cbs.append((ch, cb))
        return cbs

    def _replay_channel_history(self, graph_id: str, sink: WSFrameSink) -> None:
        env = self._engine._environments.get(graph_id)
        if env is None or not env.shared_channels.list_channels():
            return
        for ch in env.shared_channels._channels.values():
            for msg in getattr(ch, "history", []) or []:
                ts = (
                    msg.timestamp.isoformat()
                    if hasattr(msg.timestamp, "isoformat")
                    else str(msg.timestamp)
                )
                sink.send_json_nowait(
                    {
                        "type": "channel_message",
                        "source": "channel",
                        "channel": ch.name,
                        "sender": msg.sender,
                        "content": msg.content,
                        "message_id": msg.message_id,
                        "timestamp": ts,
                        "ts": time.time(),
                        "history": True,
                    }
                )

    def _find_sibling_by_name(self, creature: Any, name: str) -> Any | None:
        graph_id = creature.graph_id
        if not graph_id or graph_id not in self._engine._topology.graphs:
            return None
        graph = self._engine._topology.graphs[graph_id]
        for cid in graph.creature_ids:
            try:
                c = self._engine.get_creature(cid)
            except KeyError:
                continue
            if c.name == name or c.creature_id == name:
                return c
        return None

    def _handle_ui_reply(
        self,
        sink: WSFrameSink,
        agent: Any,
        creature_name: str,
        frame: dict,
    ) -> None:
        event_id = frame.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return
        reply = UIReply(
            event_id=event_id,
            action_id=frame.get("action_id", ""),
            values=frame.get("values") if isinstance(frame.get("values"), dict) else {},
            user=frame.get("user") if isinstance(frame.get("user"), str) else None,
            timestamp=(
                float(frame["ts"])
                if isinstance(frame.get("ts"), (int, float))
                else time.time()
            ),
        )
        try:
            _ok, ack_status = agent.output_router.submit_reply_with_status(reply)
        except Exception:
            logger.debug("submit_reply failed", exc_info=True)
            ack_status = "unknown"
        sink.send_json_nowait(
            {
                "type": "ui_reply_ack",
                "event_id": event_id,
                "status": ack_status,
                "source": creature_name,
                "ts": time.time(),
            }
        )

    async def _process_input(
        self,
        sink: WSFrameSink,
        agent: Any,
        content: Any,
        source_name: str,
    ) -> None:
        try:
            await agent.inject_input(content, source="web")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            sink.send_json_nowait(
                {
                    "type": "error",
                    "source": source_name,
                    "content": str(e),
                    "ts": time.time(),
                }
            )
            return
        sink.send_json_nowait(
            {"type": "idle", "source": source_name, "ts": time.time()}
        )


def _normalize_input_content(frame: dict) -> Any:
    content = frame.get("content")
    if isinstance(content, list):
        parts = normalize_content_parts(content) or []
        return content_parts_to_dicts(parts)
    if isinstance(content, str):
        return content
    message = frame.get("message", "")
    return message if isinstance(message, str) else ""


__all__ = ["TerrariumAttachAdapter"]
