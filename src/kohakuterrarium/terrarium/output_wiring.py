"""Terrarium-side resolver for the output-wiring framework feature.

The core layer defines the protocol and a no-op default. This module
provides the real resolver that knows how to look up targets by name
inside a running terrarium and push ``creature_output`` events straight
into their event queues.

Resolution rules:

- ``entry.to == "root"`` (magic string, see ``core.output_wiring.ROOT_TARGET``)
  resolves to the terrarium's root agent. Unknown when no root is
  configured → logged and skipped.
- Any other value is looked up as a creature name in the terrarium.
  Unknown / stopped creatures are logged and skipped.

Delivery is fire-and-forget: each target receives its event via
``asyncio.create_task(target_agent._process_event(event))`` so the
source creature's ``_finalize_processing`` never blocks on a downstream
LLM round. Plugins installed on the receiver see the event through the
existing ``on_event`` notify in ``Agent._process_event``.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.events import create_creature_output_event
from kohakuterrarium.core.output_wiring import (
    ROOT_TARGET,
    OutputWiringEntry,
    render_prompt,
)
from kohakuterrarium.terrarium.graph_identity import (
    GraphIdentityError,
    resolve_local_graph_target,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.terrarium.creature import CreatureHandle

logger = get_logger(__name__)


class TerrariumOutputWiringResolver:
    """Looks up targets inside a terrarium and dispatches events to them.

    Built once by the terrarium runtime (after all creatures and the
    optional root are constructed) and attached to every agent's
    ``_wiring_resolver`` field. Lives for the lifetime of the runtime.
    """

    def __init__(
        self,
        creatures: dict[str, "CreatureHandle"],
        root_agent: "Agent | None" = None,
        *,
        engine: Any | None = None,
    ) -> None:
        self._creatures = creatures
        self._root_agent = root_agent
        # Standalone resolvers may omit the engine; when present, its adapter
        # provides remote dispatch after local target resolution fails.
        self._engine = engine
        # Remember which unknown targets we've already warned about so
        # a mis-typed target doesn't spam the log every turn.
        self._warned_missing: set[str] = set()

    def _resolve_target(self, target: str, source: str | None = None) -> "Agent | None":
        """Map a wiring target string to an Agent, or None if unknown.

        On a local miss, consult the engine-level cross-node forwarder
        (``_output_wire_adapter.peer_for_target``) BEFORE warning. If a
        peer is found, the emission will be delivered cross-node by the
        caller (``emit``); log at DEBUG and skip the misleading WARN.
        The WARN only fires when BOTH the local lookup AND the cross-
        node peer lookup miss (i.e. the target truly does not exist
        anywhere in the cluster).
        """
        if target == ROOT_TARGET:
            root_agent = self._resolve_graph_root_agent(source)
            if root_agent is None and not self._has_cross_node_peer(target):
                self._warn_once(target, "terrarium has no root agent configured")
            return root_agent

        handle = self._resolve_handle(target, source=source)
        if handle is None:
            if not self._has_cross_node_peer(target):
                self._warn_once(target, "no such creature in the source graph")
            return None
        return handle.agent

    def _has_cross_node_peer(self, target: str) -> bool:
        """Return True iff a cross-node forwarder claims this target.

        When True, also emit a DEBUG log noting the forward. Used by
        ``_resolve_target`` to gate the "unresolved" WARN so it doesn't
        fire for cross-node emissions that are actually delivered via
        ``_output_wire_adapter.forward_event``.
        """
        forwarder = getattr(self._engine, "_output_wire_adapter", None)
        if forwarder is None:
            return False
        try:
            peer = forwarder.peer_for_target(target)
        except Exception:
            return False
        if peer is None:
            return False
        logger.debug(
            "output_wiring target forwarded cross-node",
            target=target,
            peer=peer,
        )
        return True

    def _resolve_handle(self, target: str, *, source: str = ""):
        if self._engine is None:
            handle = self._creatures.get(target)
            if handle is not None:
                return handle
            matches = [
                creature
                for creature in self._creatures.values()
                if target
                in {
                    getattr(creature, "name", None),
                    getattr(getattr(creature, "config", None), "name", None),
                    getattr(
                        getattr(getattr(creature, "agent", None), "config", None),
                        "name",
                        None,
                    ),
                }
            ]
            return matches[0] if len(matches) == 1 else None
        try:
            resolved = resolve_local_graph_target(
                self._engine._topology,
                self._creatures,
                caller_id=source,
                target=target,
            )
        except GraphIdentityError:
            return None
        return self._creatures.get(resolved.target_id)

    def _resolve_graph_root_agent(self, source: str | None) -> "Agent | None":
        source_handle = self._creatures.get(source or "")
        if source_handle is None and self._engine is None:
            source_handle = self._resolve_handle(source or "", source=source or "")
        source_graph = getattr(source_handle, "graph_id", None)
        candidates = [
            c
            for c in self._creatures.values()
            if getattr(c, "is_privileged", False)
            and (source_graph is None or getattr(c, "graph_id", None) == source_graph)
        ]
        if not candidates and self._root_agent is not None:
            return self._root_agent
        if not candidates:
            return None
        for creature in candidates:
            if getattr(creature, "creature_id", "") == ROOT_TARGET:
                return creature.agent
        for creature in candidates:
            if getattr(creature, "name", "") == ROOT_TARGET:
                return creature.agent
        return sorted(candidates, key=lambda c: getattr(c, "creature_id", ""))[0].agent

    def _target_identity(self, target: str, target_agent: "Agent") -> str:
        if target == ROOT_TARGET:
            return getattr(
                target_agent,
                "_creature_id",
                getattr(target_agent.config, "name", ROOT_TARGET),
            )
        return getattr(target_agent, "_creature_id", target)

    def _warn_once(self, target: str, reason: str) -> None:
        if target in self._warned_missing:
            return
        self._warned_missing.add(target)
        logger.warning(
            "output_wiring target unresolved - emissions will be dropped",
            target=target,
            reason=reason,
        )

    async def emit(
        self,
        *,
        source: str,
        content: str,
        source_event_type: str,
        turn_index: int,
        entries: list[OutputWiringEntry],
    ) -> None:
        """Dispatch one event per entry into the resolved target's queue.

        Fire-and-forget: tasks are created but not awaited. The source
        creature's turn-finalisation returns immediately. Exceptions
        inside ``_process_event`` on the receiver are logged by the
        receiver's own code path and do not propagate here.
        """
        for entry in entries:
            target_agent = self._resolve_target(entry.to, source=source)
            if target_agent is None:
                # Cross-node fallback: a remote forwarder may know
                # which peer hosts this name.  Only fires when an
                # engine-level adapter is installed (lab-host / lab-
                # client mode); standalone runs miss and skip as before.
                forwarder = getattr(self._engine, "_output_wire_adapter", None)
                if forwarder is not None:
                    source_graph_id = self._engine._topology.creature_to_graph.get(
                        source
                    )
                    peer = forwarder.peer_for_target(entry.to, graph_id=source_graph_id)
                    if peer is not None and source_graph_id is not None:
                        delivered_content = content if entry.with_content else ""
                        prompt_text = render_prompt(
                            entry,
                            source=source,
                            target=entry.to,
                            content=delivered_content,
                            turn_index=turn_index,
                            source_event_type=source_event_type,
                        )
                        asyncio.create_task(
                            forwarder.forward_event(
                                target_name=entry.to,
                                event={
                                    "type": "creature_output",
                                    "content": delivered_content,
                                    "context": {
                                        "source": source,
                                        "with_content": bool(entry.with_content),
                                        "source_event_type": source_event_type,
                                        "turn_index": turn_index,
                                    },
                                    "prompt_override": prompt_text,
                                },
                                source_creature_id=source,
                                source_graph_id=source_graph_id,
                            ),
                            name=f"wiring_remote_{source}_to_{entry.to}_{turn_index}",
                        )
                        continue
                continue
            target_identity = self._target_identity(entry.to, target_agent)
            if source == target_identity and not entry.allow_self_trigger:
                logger.warning(
                    "output_wiring self-trigger blocked",
                    source=source,
                    target=entry.to,
                    reason="set allow_self_trigger=true on this output_wiring entry to permit it",
                )
                continue
            if not getattr(target_agent, "_running", False):
                logger.debug(
                    "output_wiring target not running - dropping",
                    source=source,
                    target=entry.to,
                )
                continue

            delivered_content = content if entry.with_content else ""
            prompt_text = render_prompt(
                entry,
                source=source,
                target=entry.to,
                content=delivered_content,
                turn_index=turn_index,
                source_event_type=source_event_type,
            )
            event = create_creature_output_event(
                source=source,
                target=entry.to,
                content=delivered_content,
                with_content=entry.with_content,
                source_event_type=source_event_type,
                turn_index=turn_index,
                prompt_override=prompt_text,
            )
            # Runs before the delivery task so the visual cue lands first.
            notify_inbound_delivery(
                target_agent,
                source=source,
                to=entry.to,
                content=delivered_content,
                with_content=entry.with_content,
                source_event_type=source_event_type,
                turn_index=turn_index,
            )
            # Fire-and-forget: don't block the source's finalisation on
            # the target's turn-processing.
            task = asyncio.create_task(
                _safe_deliver(target_agent, event),
                name=f"wiring_{source}_to_{entry.to}_{turn_index}",
            )
            # Attach a done-callback so we can surface receiver-side
            # exceptions at warning-level (instead of the default
            # "Task exception was never retrieved" noise).
            task.add_done_callback(
                lambda t, tgt=entry.to: _log_task_error(t, source, tgt)
            )
            logger.debug(
                "output_wiring emission dispatched",
                source=source,
                target=entry.to,
                with_content=entry.with_content,
                turn_index=turn_index,
            )


def notify_inbound_delivery(
    target_agent: "Agent",
    *,
    source: str,
    to: str,
    content: str,
    with_content: bool,
    source_event_type: str,
    turn_index: int,
) -> None:
    """Surface an inbound delivery as a ``wire_inbound`` activity on the
    receiver's output bus, so its chat tab (live stream and history
    replay alike) renders an "Inbound from <source>" block instead of
    the receiver visibly starting a turn with no explanation.

    Shared by output wiring, ``group_send``, and ``group_spawn_child``
    initial-task delivery — every path that pushes a ``creature_output``
    event into another creature. Best-effort: a router without activity
    support is skipped silently.
    """
    try:
        router = getattr(target_agent, "output_router", None)
        if router is None or not hasattr(router, "notify_activity"):
            return
        preview = (content or "").strip()
        if len(preview) > 240:
            preview = preview[:239] + "…"
        router.notify_activity(
            "wire_inbound",
            f"Inbound from {source}",
            metadata={
                "from": source,
                "to": to,
                "with_content": with_content,
                "content_preview": preview,
                "source_event_type": source_event_type,
                # The SOURCE's turn number. Never emitted as ``turn_index``:
                # stream frames and store rows interpret that key as
                # receiver-timeline coordinates, and the frontend's branch
                # gate drops frames whose turn/branch pair mixes timelines.
                "source_turn_index": turn_index,
            },
        )
    except Exception:
        logger.debug(
            "wire_inbound notify failed; receiver router may not support activity emit",
        )


async def _safe_deliver(target_agent: "Agent", event) -> None:
    """Invoke target's ``_process_event`` and swallow its errors.

    The receiver has its own error handling inside ``_process_event``
    (``_process_event_with_controller`` catches and logs exceptions).
    This wrapper is a last line of defence so the task created in
    ``emit`` never propagates an error into the asyncio event loop.
    """
    try:
        await target_agent._process_event(event)
    except Exception as exc:
        logger.warning(
            "output_wiring delivery raised inside receiver",
            target=getattr(target_agent, "config", None) and target_agent.config.name,
            error=str(exc),
            exc_info=True,
        )


def _log_task_error(task: asyncio.Task, source: str, target: str) -> None:
    """Callback attached to dispatch tasks. Logs any uncaught error."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.warning(
        "output_wiring dispatch task errored",
        source=source,
        target=target,
        error=str(exc),
    )
