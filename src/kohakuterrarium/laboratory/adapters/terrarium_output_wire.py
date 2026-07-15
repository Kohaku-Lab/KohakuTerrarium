"""Route output-wiring events between Laboratory nodes.

Local targets use the in-process resolver. On a miss, workers delegate routing
to the host, whose cluster-wide resolver forwards the event to the target's
home node. Relayed messages are marked to prevent another forwarding hop.
"""

import asyncio
from typing import Any, Callable

from kohakuterrarium.core.events import create_creature_output_event
from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabNotifier
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TerrariumOutputWireAdapter:
    """Per-node extension that forwards and receives output-wiring events.

    The engine reference lets its output resolver discover this adapter without
    an import cycle. Only the host installs a cluster target resolver.
    """

    NAMESPACE = "terrarium.output_wire"

    def __init__(self, engine: Terrarium, lab_node: LabNotifier) -> None:
        self._engine = engine
        self._node = lab_node
        self._target_resolver: Callable[[str], tuple[str, str] | None] | None = None
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        engine._output_wire_adapter = self
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        if getattr(self._engine, "_output_wire_adapter", None) is self:
            self._engine._output_wire_adapter = None
        self._node.unregister_app_extension(self.NAMESPACE)
        self._target_resolver = None
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    def set_target_resolver(
        self, resolver: Callable[[str], tuple[str, str] | None]
    ) -> None:
        """Install the host's target-name to home-node lookup."""
        self._target_resolver = resolver

    def peer_for_target(self, target_name: str) -> str | None:
        """Return the remote route for a target, if one is required.

        Workers return ``"_host"`` because only the host knows cluster-wide
        placement. The host returns ``None`` for its own targets so they remain
        on the local path.
        """
        if self._target_resolver is None:
            # Workers lack cluster placement data, so the host is their relay.
            return "_host"
        try:
            entry = self._target_resolver(target_name)
        except Exception:
            logger.exception("output_wire target resolver crashed")
            return None
        if entry is None:
            return None
        node_id, _ = entry
        if not node_id or node_id == "_host":
            return None
        return node_id

    async def forward_event(
        self,
        peer_node: str,
        body: dict[str, Any],
    ) -> bool:
        """Fire ``inject`` at ``peer_node``.  Returns ``True`` on RPC ack."""
        try:
            await self._node.notify(
                to_node=peer_node,
                namespace=self.NAMESPACE,
                type="inject",
                body=body,
            )
            return True
        except Exception:
            logger.debug(
                "output_wire forward failed",
                peer=peer_node,
                target=body.get("target_name"),
            )
            return False

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.output_wire handler failed: %s", msg.type)
            return {"error": {"kind": "output_wire", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "inject":
                return await self._op_inject(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.output_wire type: {msg.type!r}",
                    }
                }

    async def _op_inject(self, body: dict[str, Any]) -> dict[str, Any]:
        """Deliver an injected event locally or relay it once from the host."""
        target_name = body.get("target_name", "")
        if not target_name:
            raise ValueError("target_name required")
        target_agent = self._resolve_local_agent(target_name)
        if target_agent is None:
            # Only the host can resolve a miss to another peer. The marker
            # prevents a misplaced target from being forwarded repeatedly.
            if self._target_resolver is not None and not body.get("relayed"):
                try:
                    entry = self._target_resolver(target_name)
                except Exception:
                    entry = None
                if entry is not None:
                    peer_node, _ = entry
                    if peer_node and peer_node != "_host":
                        relayed_body = {**body, "relayed": True}
                        await self.forward_event(peer_node, relayed_body)
                        return {"delivered": True, "relayed": peer_node}
            raise KeyError(f"no creature named {target_name!r} on this node")
        if not getattr(target_agent, "_running", False):
            return {"delivered": False, "reason": "target_not_running"}
        event = create_creature_output_event(
            source=body.get("source", ""),
            target=target_name,
            content=body.get("content", ""),
            with_content=bool(body.get("with_content", True)),
            source_event_type=body.get("source_event_type", ""),
            turn_index=int(body.get("turn_index", 0)),
            prompt_override=body.get("prompt_override", ""),
        )
        # Activity reporting must not prevent event delivery.
        try:
            router = getattr(target_agent, "output_router", None)
            if router is not None and hasattr(router, "notify_activity"):
                preview = (body.get("content", "") or "").strip()
                if len(preview) > 240:
                    preview = preview[:239] + "…"
                router.notify_activity(
                    "wire_inbound",
                    f"Inbound from {body.get('source', '?')}",
                    metadata={
                        "from": body.get("source", ""),
                        "to": target_name,
                        "with_content": bool(body.get("with_content", True)),
                        "content_preview": preview,
                        "source_event_type": body.get("source_event_type", ""),
                        "source_turn_index": int(body.get("turn_index", 0)),
                        "cross_node": True,
                    },
                )
        except Exception:
            logger.debug("wire_inbound notify failed on injected event")
        # Do not hold the sender's emit task for the receiver's entire turn.
        asyncio.create_task(target_agent._process_event(event))
        return {"delivered": True}

    def _resolve_local_agent(self, target_name: str):
        """Find an Agent on this engine by creature_id, name, or config.name."""
        for creature in self._engine.list_creatures():
            if creature.creature_id == target_name:
                return creature.agent
            if getattr(creature, "name", None) == target_name:
                return creature.agent
            cfg = getattr(creature.agent, "config", None)
            if getattr(cfg, "name", None) == target_name:
                return creature.agent
        return None


__all__ = ["TerrariumOutputWireAdapter"]
