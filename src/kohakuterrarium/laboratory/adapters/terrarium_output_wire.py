"""APP extension adapter for ``terrarium.output_wire``.

Cross-node output-wiring forwarder.  Output wiring (creature A's
``output_wiring`` entry targeting creature B) is normally resolved
locally: the source's :class:`TerrariumOutputWiringResolver` looks up
B in the same engine's creature dict and delivers a synthesized
``creature_output_event`` into B's event queue.  When B lives on a
different node, the local lookup misses; this adapter provides the
fallback path:

- The resolver, finding no local target, checks for an installed
  forwarder on the engine (``engine._output_wire_adapter``).  If
  present, it asks the adapter for the peer hosting the target name.
- A peer is found via the controller-installed resolver callback
  (``set_target_resolver``).  Workers do not have a resolver — only
  the controller knows the cluster's target-name → home-node mapping.
- If a peer is found, the adapter packages the event data and fires
  ``terrarium.output_wire.inject`` to that node.  The peer's adapter
  looks up the creature by name locally, reconstructs the event, and
  calls :meth:`Agent._process_event` — identical to what the local
  resolver would have done in-process.

Loops are prevented by tagging the event with ``injected=True`` in the
RPC body; the receiver delivers and does NOT re-fire output_wiring
emissions for that target because the receiver runs its own turn that
may or may not produce its own wiring fan-out, and the framework's
existing self-trigger guard handles cycle-detection at the resolver
layer.  No additional cross-node loop guard is needed because the
adapter only forwards on a *miss*, never on a successful local
resolution.

Wire shape of the ``inject`` body:

    {
        "target_name": str,        # the entry.to value
        "source": str,             # source creature name
        "content": str,
        "with_content": bool,
        "source_event_type": str,
        "turn_index": int,
        "prompt_override": str,
    }
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
    """Per-node ``terrarium.output_wire`` extension.

    Both controller (lab host) and workers (lab clients) install one.
    Only the controller wires a real target resolver; workers leave it
    unset (their adapter only RECEIVES forwarded events — they never
    originate cross-node forwards because their local resolver runs
    against the worker's own creatures only, and the worker has no
    cluster-wide view of where other creatures live).

    Stashes itself on the engine as ``_output_wire_adapter`` so the
    output-wiring resolver finds it without an import cycle.
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
        """Install a ``target_name -> (node_id, creature_id) | None`` lookup.

        The controller's multi-node service installs this once Lab
        clients are tracked, driven by its ``_home`` registry +
        creature-name index.  Workers do not install one.
        """
        self._target_resolver = resolver

    def peer_for_target(self, target_name: str) -> str | None:
        """Return the peer node id hosting ``target_name``, or ``None``.

        On the **host**: consults the cluster name resolver installed
        by :class:`MultiNodeTerrariumService`.  Resolver returning
        ``(node_id, _)`` with ``node_id == "_host"`` means "the host's
        own engine" — the local engine path is preferred over a
        cross-node forward to ourselves, so we surface ``None``.

        On a **worker** (no resolver installed): always returns
        ``"_host"`` so unresolvable local emissions get forwarded to
        the host, which re-routes via its cluster resolver to the
        right peer.  This is the "Lab host = transparent relay"
        invariant: workers don't need to know about peer creatures;
        the host is the routing fabric.
        """
        if self._target_resolver is None:
            # Worker mode — delegate to host as the cluster relay.
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

    # ------------------------------------------------------------------
    # APP dispatch
    # ------------------------------------------------------------------

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
        """Replay a forwarded output-wiring delivery into a local creature.

        Cluster relay: when this adapter has a target resolver installed
        (host mode) AND the target isn't local AND the resolver knows a
        peer for the name, forward the inject onward to that peer.  A
        worker that doesn't know about peer creatures forwards to the
        host on miss; the host re-routes here.  This is the
        "Lab host = transparent relay" UX invariant for the output-
        wiring fabric.

        Loop guard: ``body.get("relayed")`` is set when the host
        re-routes — a peer that receives a relayed inject and STILL
        can't resolve locally raises rather than double-forwarding.
        """
        target_name = body.get("target_name", "")
        if not target_name:
            raise ValueError("target_name required")
        target_agent = self._resolve_local_agent(target_name)
        if target_agent is None:
            # Cluster relay: only the host installs a resolver.  If we
            # have one and the target lives on a peer, forward there.
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
        # Best-effort surface a "wire_inbound" activity on the
        # receiver's router so the chat UI gets the same visual cue it
        # would for a local cross-creature wire.
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
                        "turn_index": int(body.get("turn_index", 0)),
                        "cross_node": True,
                    },
                )
        except Exception:
            logger.debug("wire_inbound notify failed on injected event")
        # Fire-and-forget delivery — match the local resolver's pattern.
        # Awaiting here would tie the controller's emit task to the
        # receiver's entire turn.
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
