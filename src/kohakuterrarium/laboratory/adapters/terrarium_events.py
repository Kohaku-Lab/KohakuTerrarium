"""APP extension adapter for ``terrarium.events``.

Producer side of the streaming protocol described in
:mod:`kohakuterrarium.laboratory.streams`.  Lives on a worker node
alongside :class:`TerrariumRuntimeAdapter`.  Handles three stream
starts (``start_chat``, ``start_subscribe``) plus the universal
``cancel_stream`` teardown.

Each ``start_*`` request spawns a background task that pumps events
from the local engine into ``terrarium.stream`` APP frames addressed
back to the consumer (typically the controller-side host).  On
producer-side natural end the task emits an ``eof`` frame; on
exception, an ``error`` frame.  ``cancel_stream`` cancels the task
and clears bookkeeping.

The consumer is identified by ``AppMessage.sender_node`` of the
``start_*`` request; we send frames back to that node.
"""

import asyncio
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabNode
from kohakuterrarium.laboratory.streams import StreamDemux
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.wire import (
    pack_engine_event,
    unpack_content,
    unpack_event_filter,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TerrariumEventsAdapter:
    """Producer-side adapter for ``terrarium.events`` streams.

    Args:
        engine: the local :class:`Terrarium` engine providing events.
        lab_node: a :class:`ClientConnector` or :class:`HostEngine` —
            anything that satisfies the :class:`LabNode` protocol.
    """

    NAMESPACE = "terrarium.events"

    def __init__(self, engine: Terrarium, lab_node: LabNode) -> None:
        self._engine = engine
        self._node = lab_node
        self._active: dict[str, asyncio.Task] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        """Unregister + cancel every in-flight stream. Idempotent."""
        self._node.unregister_app_extension(self.NAMESPACE)
        active_count = len(self._active)
        for task in self._active.values():
            if not task.done():
                task.cancel()
        self._active.clear()
        logger.info(
            "lab adapter detached",
            namespace=self.NAMESPACE,
            cancelled_streams=active_count,
        )

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.events handler failed: %s", msg.type)
            return {"error": {"kind": "events", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "start_chat":
                stream_id = msg.body["stream_id"]
                creature_id = msg.body["creature_id"]
                message = unpack_content(msg.body["message"])
                consumer = msg.sender_node
                # Verify the creature exists *before* returning so the
                # consumer gets a useful synchronous error.
                self._engine.get_creature(creature_id)
                task = asyncio.create_task(
                    self._pump_chat(stream_id, creature_id, message, consumer)
                )
                self._active[stream_id] = task
                return {"started": True, "stream_id": stream_id}

            case "start_subscribe":
                stream_id = msg.body["stream_id"]
                filter_ = unpack_event_filter(msg.body.get("filter"))
                consumer = msg.sender_node
                task = asyncio.create_task(
                    self._pump_subscribe(stream_id, filter_, consumer)
                )
                self._active[stream_id] = task
                return {"started": True, "stream_id": stream_id}

            case "cancel_stream":
                stream_id = msg.body["stream_id"]
                task = self._active.pop(stream_id, None)
                if task is not None and not task.done():
                    task.cancel()
                return {"cancelled": True, "stream_id": stream_id}

            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.events type: {msg.type!r}",
                    }
                }

    # ------------------------------------------------------------------
    # Pump tasks
    # ------------------------------------------------------------------

    async def _pump_chat(
        self,
        stream_id: str,
        creature_id: str,
        message: Any,
        consumer: str,
    ) -> None:
        try:
            creature = self._engine.get_creature(creature_id)
            async for token in creature.chat(message):
                await self._send_frame(
                    consumer, {"stream_id": stream_id, "token": token}
                )
            await self._send_frame(consumer, {"stream_id": stream_id, "eof": True})
        except asyncio.CancelledError:
            # Consumer asked us to stop. Don't send EOF — they're gone.
            raise
        except Exception as e:
            await self._send_frame(
                consumer,
                {
                    "stream_id": stream_id,
                    "error": {"kind": "engine", "message": str(e)},
                },
            )
        finally:
            self._active.pop(stream_id, None)

    async def _pump_subscribe(
        self,
        stream_id: str,
        filter_: Any,
        consumer: str,
    ) -> None:
        try:
            async for ev in self._engine.subscribe(filter_):
                await self._send_frame(
                    consumer,
                    {"stream_id": stream_id, "event": pack_engine_event(ev)},
                )
            await self._send_frame(consumer, {"stream_id": stream_id, "eof": True})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._send_frame(
                consumer,
                {
                    "stream_id": stream_id,
                    "error": {"kind": "events", "message": str(e)},
                },
            )
        finally:
            self._active.pop(stream_id, None)

    async def _send_frame(self, consumer: str, body: dict[str, Any]) -> None:
        try:
            await self._node.notify(
                to_node=consumer,
                namespace=StreamDemux.NAMESPACE,
                type="frame",
                body=body,
            )
        except Exception:
            logger.debug(
                "failed to deliver frame to %s for stream %s",
                consumer,
                body.get("stream_id"),
            )


__all__ = ["TerrariumEventsAdapter"]
