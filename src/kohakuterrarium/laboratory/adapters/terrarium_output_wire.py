"""Route output-wiring events between Laboratory nodes.

Local targets use the in-process resolver. On a miss, workers delegate routing
to the host, whose cluster-wide resolver forwards the event to the target's
home node. Relayed messages are marked to prevent another forwarding hop.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabNode
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_NAMESPACE = "terrarium.output_wire"
_MSG_INJECT = "inject"


class TerrariumOutputWireAdapter:
    """Route graph-bound output events with exact identities and acknowledgements."""

    def __init__(
        self,
        engine: Any,
        messenger: LabNode,
        *,
        name_cache: dict[str, set[tuple[str, str, str]]] | None = None,
        cluster_members: Callable[[str], set[str]] | None = None,
    ) -> None:
        self._engine = engine
        self._messenger = messenger
        self._name_cache = name_cache if name_cache is not None else {}
        self._cluster_members = cluster_members or (lambda graph_id: {graph_id})
        register = getattr(messenger, "register_app_extension", None)
        if register is None:
            register = getattr(messenger, "register_handler", None)
        if register is None:
            raise TypeError("messenger does not support APP extensions")
        register(_NAMESPACE, self._dispatch)
        engine._output_wire_adapter = self

    def detach(self) -> None:
        if getattr(self._engine, "_output_wire_adapter", None) is self:
            self._engine._output_wire_adapter = None
        unregister = getattr(self._messenger, "unregister_app_extension", None)
        if unregister is None:
            unregister = getattr(self._messenger, "unregister_handler", None)
        if unregister is not None:
            unregister(_NAMESPACE)

    def _node_id(self) -> str:
        value = getattr(self._messenger, "node_id", None)
        if callable(value):
            value = value()
        if not value:
            value = getattr(self._messenger, "client_id", None)
        if not value:
            value = "_host"
        return str(value)

    def set_name_cache(self, cache: dict[str, set[tuple[str, str, str]]]) -> None:
        self._name_cache = cache

    def set_cluster_members(self, resolver: Callable[[str], set[str]]) -> None:
        self._cluster_members = resolver

    def peer_for_target(
        self, target_name: str, *, graph_id: str | None = None
    ) -> str | None:
        if self._node_id() != "_host":
            return "_host"
        entries = set(self._name_cache.get(target_name, set()))
        if graph_id is not None:
            graph_ids = self._cluster_members(graph_id)
            entries = {entry for entry in entries if entry[0] in graph_ids}
        return next(iter(entries))[1] if len(entries) == 1 else None

    async def forward_event(
        self,
        *,
        target_name: str,
        event: dict[str, Any],
        source_creature_id: str | None = None,
        source_graph_id: str | None = None,
        hop: int = 0,
    ) -> bool:
        if not source_creature_id or not source_graph_id or hop != 0:
            return False
        peer = self.peer_for_target(target_name, graph_id=source_graph_id)
        if peer is None or peer == self._node_id():
            return False
        result = await self._request(
            peer,
            _MSG_INJECT,
            {
                "target_name": target_name,
                "event": dict(event),
                "source_creature_id": source_creature_id,
                "source_graph_id": source_graph_id,
                "hop": hop,
                "peer": self._node_id(),
            },
        )
        return bool(result.get("delivered", False))

    async def _request(
        self, peer: str, msg_type: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        request = self._messenger.request
        try:
            return await request(peer, msg_type, body)
        except TypeError:
            return await request(
                to_node=peer,
                namespace=_NAMESPACE,
                type=msg_type,
                body=body,
            )

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            if msg.type != _MSG_INJECT:
                raise ValueError(f"unknown output-wire op: {msg.type}")
            body = dict(msg.body)
            body["_sender_node"] = msg.sender_node
            return await self._op_inject(body)
        except Exception as exc:
            logger.warning("output-wire op failed", error=str(exc))
            return {"delivered": False, "error": str(exc)}

    async def _op_inject(self, body: dict[str, Any]) -> dict[str, Any]:
        target_name = body.get("target_name")
        source_id = body.get("source_creature_id")
        source_graph_id = body.get("source_graph_id")
        hop = body.get("hop")
        peer_claim = body.get("peer")
        sender_node = body.get("_sender_node")
        if (
            not isinstance(target_name, str)
            or not isinstance(source_id, str)
            or not isinstance(source_graph_id, str)
            or not isinstance(hop, int)
            or hop < 0
            or hop > 1
            or peer_claim != sender_node
        ):
            return {"delivered": False, "error": "invalid routing identity"}

        if self._node_id() == "_host":
            source_identity = (
                source_graph_id,
                sender_node,
                source_id,
            )
            if source_identity not in self._name_cache.get(source_id, set()):
                return {"delivered": False, "error": "forged source identity"}
            event = body.get("event", {})
            event_source = event.get("source")
            context_source = event.get("context", {}).get("source")
            if event_source not in {None, source_id} or context_source not in {
                None,
                source_id,
            }:
                return {"delivered": False, "error": "forged event source"}
            graph_ids = self._cluster_members(source_graph_id)
            entries = {
                entry
                for entry in self._name_cache.get(target_name, set())
                if entry[0] in graph_ids
            }
            if len(entries) != 1 or hop != 0:
                return {
                    "delivered": False,
                    "error": "target not found or ambiguous",
                }
            target_graph_id, peer, target_creature_id = next(iter(entries))
            result = await self._request(
                peer,
                _MSG_INJECT,
                {
                    "target_name": target_name,
                    "event": dict(body.get("event", {})),
                    "source_creature_id": source_id,
                    "source_graph_id": source_graph_id,
                    "target_graph_id": target_graph_id,
                    "target_creature_id": target_creature_id,
                    "hop": 1,
                    "peer": self._node_id(),
                },
            )
            return {"delivered": bool(result.get("delivered", False))}

        if sender_node != "_host" or hop != 1:
            return {"delivered": False, "error": "invalid relay peer"}
        target_creature_id = body.get("target_creature_id")
        target_graph_id = body.get("target_graph_id")
        if not isinstance(target_creature_id, str) or not isinstance(
            target_graph_id, str
        ):
            return {"delivered": False, "error": "missing exact target identity"}
        target_agent, local_graph_id = self._resolve_local_target(target_creature_id)
        if target_agent is None:
            return {"delivered": False, "error": "target not found locally"}
        if local_graph_id != target_graph_id:
            return {"delivered": False, "error": "target graph mismatch"}
        if not getattr(target_agent, "_running", False):
            return {"delivered": False, "error": "target not running"}
        event = _event_from_dict(dict(body.get("event", {})))
        task = asyncio.create_task(
            target_agent._process_event(event),
            name=f"output-wire-{source_id}-to-{target_creature_id}",
        )
        task.add_done_callback(_log_delivery_error)
        return {"delivered": True}

    def _resolve_local_target(
        self, target_creature_id: str
    ) -> tuple[Any | None, str | None]:
        creature = getattr(self._engine, "_creatures", {}).get(target_creature_id)
        if creature is None:
            return None, None
        return getattr(creature, "agent", None), getattr(creature, "graph_id", None)


def _event_from_dict(data: dict[str, Any]) -> TriggerEvent:
    """Rebuild the minimal TriggerEvent used for output-wire injection."""
    return TriggerEvent(
        type=str(data.get("type", "creature_output")),
        content=str(data.get("content", "")),
        context=dict(data.get("context", data.get("metadata", {}))),
        prompt_override=data.get("prompt_override"),
    )


def _log_delivery_error(task: "asyncio.Task[Any]") -> None:
    """Consume background receiver errors without leaking task warnings."""
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("output-wire receiver event failed")
