"""TerrariumService — runtime abstraction Studio depends on.

The :class:`TerrariumService` Protocol captures every operation Studio
performs on a terrarium runtime. Three implementations:

- :class:`LocalTerrariumService` — direct in-process calls on a
  :class:`~kohakuterrarium.terrarium.engine.Terrarium`. Used in
  single-host deployments. Zero serialization, zero envelope overhead.
- :class:`~kohakuterrarium.terrarium.remote_service.RemoteTerrariumService`
  — Lab APP-backed proxy. Same Protocol surface; method calls translate
  to APP envelopes against the ``terrarium.runtime`` namespace on a
  target node.
- :class:`~kohakuterrarium.terrarium.multi_node_service.MultiNodeTerrariumService`
  — composite for ``lab-host`` mode: one local engine plus N remote
  workers, with a ``creature_id → home_node`` registry that routes
  per-creature ops and fans out global reads.

API routes that need cluster-wide visibility take the service via
``Depends(get_service)``. Routes that still call ``engine.*`` directly
(``as_engine(service)``) operate on the host's local engine only.

DTOs vs live objects:

- :class:`CreatureInfo` is a frozen, msgpack-serializable snapshot of
  a creature's identity + topology binding. Used for cross-process
  transit and for code that only needs identity/topology data.
- The :attr:`LocalTerrariumService.engine` escape hatch returns the
  live :class:`~kohakuterrarium.terrarium.engine.Terrarium` for local
  callers that need access not on the Protocol (rare; primarily
  inside Phase 0 before W1 migration).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.core.channel import ChannelMessage as _ChannelMessage
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.creature_ops import (
    agent_env as _agent_env,
    agent_execute_command as _agent_execute_command,
    agent_get_module_options as _agent_get_module_options,
    agent_get_native_tool_options as _agent_get_native_tool_options,
    agent_list_modules as _agent_list_modules,
    agent_list_plugins as _agent_list_plugins,
    agent_native_tool_inventory as _agent_native_tool_inventory,
    agent_patch_scratchpad as _agent_patch_scratchpad,
    agent_scratchpad as _agent_scratchpad,
    agent_set_module_options as _agent_set_module_options,
    agent_set_native_tool_options as _agent_set_native_tool_options,
    agent_set_working_dir as _agent_set_working_dir,
    agent_system_prompt as _agent_system_prompt,
    agent_toggle_module as _agent_toggle_module,
    agent_toggle_plugin as _agent_toggle_plugin,
    agent_triggers as _agent_triggers,
    agent_working_dir as _agent_working_dir,
    attach_policies_for as _attach_policies_for,
    build_runtime_graph_snapshot_for as _build_runtime_graph_snapshot_for,
    chat_branches_for as _chat_branches_for,
    chat_history_for as _chat_history_for,
    normalize_command_args as _normalize_command_args,
    session_attach_policies_for as _session_attach_policies_for,
    wire_creature_on_engine as _wire_creature_on_engine,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
)
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
)


@dataclass(frozen=True)
class CreatureInfo:
    """Identity + topology snapshot of a single creature.

    Serializable; safe to send over Lab. The live
    :class:`~kohakuterrarium.terrarium.creature_host.Creature` object
    is *not* serializable (holds Agent, channels, etc.), so the
    Protocol returns this DTO instead.
    """

    creature_id: str
    name: str
    graph_id: str
    is_running: bool
    is_privileged: bool
    parent_creature_id: str | None
    listen_channels: tuple[str, ...]
    send_channels: tuple[str, ...]
    # Resolved LLM model + canonical id (B3/B4: empty == deferred).
    model: str = ""
    llm_name: str = ""


def _channel_message_to_dict(m: Any) -> dict[str, Any]:
    """Serialize a :class:`ChannelMessage` to a JSON-friendly dict.

    Used by ``channel_history`` so the API surface returns the same
    shape on both local and remote service paths.  ``timestamp`` is
    ISO-8601 (or empty when the field isn't a datetime); ``content``
    is passed through verbatim (string or list-of-parts).
    """
    ts = getattr(m, "timestamp", None)
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
    return {
        "message_id": getattr(m, "message_id", ""),
        "sender": getattr(m, "sender", ""),
        "sender_id": getattr(m, "sender_id", None),
        "content": getattr(m, "content", ""),
        "channel": getattr(m, "channel", None),
        "timestamp": ts_str,
    }


def creature_to_info(creature: Creature) -> CreatureInfo:
    """Build a :class:`CreatureInfo` snapshot from a live Creature."""
    agent = getattr(creature, "agent", None)
    llm = getattr(agent, "llm", None) if agent is not None else None
    model = (
        getattr(llm, "model", "")
        or getattr(getattr(llm, "config", None), "model", "")
        or (getattr(agent, "config", None) and getattr(agent.config, "model", ""))
        or ""
    )
    # Canonical "provider/name" — falls back to raw model so the modal
    # never shows "No model" when one IS bound (B3/B4).
    llm_name = ""
    get_ident = getattr(agent, "llm_identifier", None) if agent is not None else None
    if callable(get_ident):
        try:
            llm_name = get_ident() or ""
        except Exception:
            pass
    llm_name = llm_name or str(model or "")
    return CreatureInfo(
        creature_id=creature.creature_id,
        name=creature.name,
        graph_id=creature.graph_id,
        is_running=creature.is_running,
        is_privileged=creature.is_privileged,
        parent_creature_id=creature.parent_creature_id,
        listen_channels=tuple(creature.listen_channels),
        send_channels=tuple(creature.send_channels),
        model=str(model or ""),
        llm_name=str(llm_name or ""),
    )


@runtime_checkable
class TerrariumService(Protocol):
    """Operations Studio needs from a terrarium runtime.

    Method semantics match the underlying
    :class:`~kohakuterrarium.terrarium.engine.Terrarium` engine
    exactly; an implementation that diverges from engine behavior is
    a bug.
    """

    @property
    def node_id(self) -> str:
        """Identifier of the node this service represents.

        Single-host deployments use ``"_host"`` (the Lab convention).
        Multi-node deployments use the assigned Lab client id.
        """
        ...

    # ------------------------------------------------------------------
    # Topology / status reads (sync data, async signature for uniformity
    # with the Remote impl that goes over the wire)
    # ------------------------------------------------------------------

    async def list_creatures(self) -> tuple[CreatureInfo, ...]: ...

    async def get_creature_info(self, creature_id: str) -> CreatureInfo | None: ...

    async def list_graphs(self) -> tuple[GraphTopology, ...]: ...

    async def get_graph(self, graph_id: str) -> GraphTopology | None: ...

    async def list_channels(self, graph_id: str) -> tuple[ChannelInfo, ...]: ...

    async def creature_status(self, creature_id: str) -> dict[str, Any] | None: ...

    async def status_snapshot(self) -> dict[str, Any]:
        """Engine-wide rolled-up status (matches ``Terrarium.status()`` with no args)."""
        ...

    # === Lifecycle ===

    async def add_creature(
        self,
        config: Any,
        *,
        graph_id: str | None = None,
        creature_id: str | None = None,
        is_privileged: bool = False,
        parent_creature_id: str | None = None,
        start: bool = True,
        pwd: str | None = None,
        llm_override: Any = None,
        on_node: str = "_host",
        name: str | None = None,
    ) -> CreatureInfo:
        """Add a creature.

        ``name`` is the spawn-time display-name override (the name the
        user typed); when set, the spawned creature carries it instead
        of the config file's own ``name`` — on any node.

        ``on_node`` (default ``"_host"``) is honored only by
        :class:`MultiNodeTerrariumService` — local and remote services
        accept it and verify it matches their own ``node_id``,
        raising ``ValueError`` on mismatch so misrouting surfaces loudly.
        """
        ...

    async def remove_creature(self, creature_id: str) -> None: ...

    async def start_creature(self, creature_id: str) -> None: ...

    async def stop_creature(self, creature_id: str) -> None: ...

    async def shutdown(self) -> None: ...

    # === Channels ===

    async def add_channel(
        self,
        graph_id: str,
        name: str,
        description: str = "",
    ) -> ChannelInfo: ...

    async def remove_channel(self, graph_id: str, name: str) -> TopologyDelta: ...

    async def channel_history(
        self,
        graph_id: str,
        name: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the message log for a channel.

        Each entry is a plain dict suitable for HTTP serialization:
        ``{message_id, sender, sender_id, content, timestamp}``.  Returns
        ``[]`` when the channel exists but has never received a message.
        Raises ``KeyError`` when the graph or channel is unknown.  In
        multi-node mode the call routes to the graph's home worker so the
        history reflects the channel object that actually lives there.
        """
        ...

    async def send_channel_message(
        self,
        graph_id: str,
        name: str,
        content: str | list[dict[str, Any]],
        *,
        sender: str = "human",
    ) -> str:
        """Send a message into a channel and return the new ``message_id``.

        Routes to the graph's home node so lab-host mode can deliver to
        a worker-hosted channel.  Raises ``KeyError`` when the graph is
        unknown to any node; ``ValueError`` when the channel is missing
        from the graph.
        """
        ...

    async def connect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> ConnectionResult: ...

    async def disconnect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> DisconnectionResult: ...

    # === Interaction ===

    async def inject_input(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
        *,
        source: str = "chat",
    ) -> None: ...

    def chat(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Inject ``message`` and stream the agent's text response."""
        ...

    # === Per-creature control (per ``api-lab-design.md`` §2) ===

    async def interrupt(self, creature_id: str) -> None:
        """Interrupt the creature's current controller turn.

        Cancels every direct (non-promoted) job; promoted/background
        jobs keep running. Idempotent — a no-op if nothing is in
        flight. Routes by ``_home`` in multi-node mode.
        """
        ...

    async def list_jobs(self, creature_id: str) -> list[dict[str, Any]]:
        """Return the creature's running tool + sub-agent jobs."""
        ...

    async def stop_job(self, creature_id: str, job_id: str) -> bool:
        """Cancel one running tool / sub-agent job. Returns ``True`` on hit."""
        ...

    async def promote_job(self, creature_id: str, job_id: str) -> bool:
        """Promote a running direct job to background. Returns ``True`` on hit."""
        ...

    # === Per-creature chat ops (route-by-home) ===

    async def chat_history(self, creature_id: str) -> dict[str, Any]:
        """Return conversation snapshot + event log for replay."""
        ...

    async def chat_branches(self, creature_id: str) -> list[dict[str, Any]]:
        """Return per-turn branch metadata for the chat tree."""
        ...

    async def regenerate(
        self,
        creature_id: str,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        """Regenerate an assistant response (whole tail by default).

        ``turn_index`` opens a new branch under a specific turn instead
        of regenerating the tail.  ``branch_view`` lets the caller
        retry on a non-latest branch.  Mirrors
        ``Agent.regenerate_last_response``.
        """
        ...

    async def edit_message(
        self,
        creature_id: str,
        msg_idx: int,
        content: str | list[dict[str, Any]],
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> bool:
        """Edit the user message at ``msg_idx`` and re-run from there."""
        ...

    async def rewind(self, creature_id: str, msg_idx: int) -> None:
        """Drop messages from ``msg_idx`` onward without re-running."""
        ...

    # === Per-creature state ops (route-by-home) ===

    async def get_scratchpad(self, creature_id: str) -> dict[str, str]: ...

    async def patch_scratchpad(
        self,
        creature_id: str,
        updates: dict[str, str | None],
    ) -> dict[str, str]: ...

    async def list_triggers(self, creature_id: str) -> list[dict[str, Any]]: ...

    async def get_env(self, creature_id: str) -> dict[str, Any]: ...

    async def get_system_prompt(self, creature_id: str) -> dict[str, str]: ...

    async def get_working_dir(self, creature_id: str) -> str: ...

    async def set_working_dir(self, creature_id: str, new_path: str) -> str: ...

    async def native_tool_inventory(self, creature_id: str) -> list[dict[str, Any]]: ...

    async def get_native_tool_options(
        self, creature_id: str
    ) -> dict[str, dict[str, Any]]: ...

    async def set_native_tool_options(
        self,
        creature_id: str,
        tool: str,
        values: dict[str, Any],
    ) -> dict[str, Any]: ...

    # === Per-creature mutation ops (route-by-home) ===

    async def switch_model(self, creature_id: str, model: str) -> str: ...

    async def list_plugins(self, creature_id: str) -> list[dict[str, Any]]: ...

    async def toggle_plugin(
        self,
        creature_id: str,
        plugin_name: str,
        enabled: bool,
    ) -> dict[str, Any]: ...

    # Module catalog (plugin / native_tool / future MCP) — uses a
    # studio-tier registry on the worker side (the adapter imports
    # ``studio.sessions.creature_modules`` directly since the laboratory
    # tier is unmanaged).  Protocol stays clean: callers see typed
    # methods; the wire dispatches a single ``creature_op`` shape.

    async def list_modules(self, creature_id: str) -> list[dict[str, Any]]: ...

    async def get_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]: ...

    async def set_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def toggle_module(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]: ...

    async def execute_command(
        self,
        creature_id: str,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def wire_creature(
        self,
        graph_id: str,
        creature_id: str,
        channel: str,
        direction: str,
        *,
        enabled: bool = True,
    ) -> None:
        """Toggle a single creature's listen / send edge on a channel.

        Per-side primitive used by cross-node connect — each node
        wires its own creature half of the topology.  Routes by
        creature home; the creature MUST already exist on the node.
        """
        ...

    # === Per-creature wiring (route-by-home) ===

    async def list_output_wiring(self, creature_id: str) -> list[dict[str, Any]]: ...

    async def wire_output(
        self,
        creature_id: str,
        target: str | dict[str, Any],
    ) -> dict[str, Any]: ...

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool: ...

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool: ...

    # === Attach policies (route-by-home / route-by-graph-home) ===

    async def attach_policies(self, creature_id: str) -> list[str]: ...

    async def session_attach_policies(self, session_id: str) -> list[str]: ...

    # === Cluster aggregation (multi-node fan-out) ===

    async def runtime_graph_snapshot(self) -> dict[str, Any]:
        """Normalized per-graph snapshot, suitable for the graph editor.

        :class:`MultiNodeTerrariumService` fans out across nodes and
        annotates each graph with its ``node_id``.  Local services
        return a single-node snapshot tagged with their ``node_id``.
        """
        ...

    # === Events ===

    def subscribe(
        self,
        filter: EventFilter | None = None,
    ) -> AsyncIterator[EngineEvent]: ...


class LocalTerrariumService:
    """Direct in-process implementation backed by a :class:`Terrarium`.

    Every method delegates to the underlying engine with at most a
    DTO conversion at the boundary. Behavior is identical to calling
    the engine directly; this class exists purely so Studio depends
    on a stable interface that has both a local and a (future)
    remote implementation.

    Single-host deployments instantiate this service with the
    engine; Studio sees the same operations whether Lab is involved
    or not.

    Attributes:
        node_id: ``"_host"`` for single-host deployments.
        engine: The underlying live :class:`Terrarium`. **Local-only
            escape hatch.** Code that touches ``engine`` directly is
            bound to single-host mode and won't work behind Lab.
            Avoid in new code; prefer the Protocol surface.
    """

    def __init__(self, engine: Terrarium, *, node_id: str = "_host") -> None:
        self._engine = engine
        self._node_id = node_id

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def engine(self) -> Terrarium:
        """The underlying engine. Local-only escape hatch."""
        return self._engine

    # === Topology / status reads ===

    async def list_creatures(self) -> tuple[CreatureInfo, ...]:
        return tuple(creature_to_info(c) for c in self._engine.list_creatures())

    async def get_creature_info(self, creature_id: str) -> CreatureInfo | None:
        try:
            creature = self._engine.get_creature(creature_id)
        except KeyError:
            return None
        return creature_to_info(creature)

    async def list_graphs(self) -> tuple[GraphTopology, ...]:
        return tuple(self._engine.list_graphs())

    async def get_graph(self, graph_id: str) -> GraphTopology | None:
        try:
            return self._engine.get_graph(graph_id)
        except KeyError:
            return None

    async def list_channels(self, graph_id: str) -> tuple[ChannelInfo, ...]:
        try:
            graph = self._engine.get_graph(graph_id)
        except KeyError:
            return ()
        return tuple(graph.channels.values())

    async def creature_status(self, creature_id: str) -> dict[str, Any] | None:
        try:
            return self._engine.status(creature_id)
        except KeyError:
            return None

    async def status_snapshot(self) -> dict[str, Any]:
        return self._engine.status()

    # === Lifecycle ===

    async def add_creature(
        self,
        config: Any,
        *,
        graph_id: str | None = None,
        creature_id: str | None = None,
        is_privileged: bool = False,
        parent_creature_id: str | None = None,
        start: bool = True,
        pwd: str | None = None,
        llm_override: Any = None,
        on_node: str = "_host",
        name: str | None = None,
    ) -> CreatureInfo:
        if on_node != self._node_id:
            raise ValueError(
                f"on_node={on_node!r} mismatches LocalTerrariumService "
                f"node_id={self._node_id!r}; caller must route through "
                "MultiNodeTerrariumService for multi-node spawns"
            )
        creature = await self._engine.add_creature(
            config,
            graph=graph_id,
            creature_id=creature_id,
            llm_override=llm_override,
            pwd=pwd,
            start=start,
            is_privileged=is_privileged,
            parent_creature_id=parent_creature_id,
            name=name,
        )
        return creature_to_info(creature)

    async def remove_creature(self, creature_id: str) -> None:
        await self._engine.remove_creature(creature_id)

    async def start_creature(self, creature_id: str) -> None:
        await self._engine.start(creature_id)

    async def stop_creature(self, creature_id: str) -> None:
        await self._engine.stop(creature_id)

    async def shutdown(self) -> None:
        await self._engine.shutdown()

    # === Channels ===

    async def add_channel(
        self,
        graph_id: str,
        name: str,
        description: str = "",
    ) -> ChannelInfo:
        return await self._engine.add_channel(graph_id, name, description)

    async def remove_channel(self, graph_id: str, name: str) -> TopologyDelta:
        return await self._engine.remove_channel(graph_id, name)

    async def channel_history(
        self,
        graph_id: str,
        name: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        env = self._engine._environments.get(graph_id)
        if env is None:
            raise KeyError(f"graph {graph_id!r} not found")
        ch = env.shared_channels.get(name)
        if ch is None:
            raise KeyError(f"channel {name!r} not in graph {graph_id!r}")
        messages = list(getattr(ch, "history", []) or [])
        if limit is not None and limit >= 0:
            messages = messages[-limit:]
        return [_channel_message_to_dict(m) for m in messages]

    async def send_channel_message(
        self,
        graph_id: str,
        name: str,
        content: str | list[dict[str, Any]],
        *,
        sender: str = "human",
    ) -> str:
        # Local-mode imports kept module-level (ChannelMessage); avoid
        # in-function imports per CLAUDE.md.
        env = self._engine._environments.get(graph_id)
        if env is None:
            raise KeyError(f"graph {graph_id!r} not found")
        ch = env.shared_channels.get(name)
        if ch is None:
            available = env.shared_channels.list_channels()
            raise ValueError(f"Channel {name!r} not found. Available: {available}")
        msg = _ChannelMessage(sender=sender, content=content)
        await ch.send(msg)
        return msg.message_id

    async def connect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> ConnectionResult:
        return await self._engine.connect(sender_id, receiver_id, channel=channel)

    async def disconnect(
        self,
        sender_id: str,
        receiver_id: str,
        *,
        channel: str | None = None,
    ) -> DisconnectionResult:
        return await self._engine.disconnect(sender_id, receiver_id, channel=channel)

    # === Interaction ===

    async def inject_input(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
        *,
        source: str = "chat",
    ) -> None:
        creature = self._engine.get_creature(creature_id)
        await creature.inject_input(message, source=source)

    def chat(
        self,
        creature_id: str,
        message: str | list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        # Resolving the creature is sync; the returned iterator is the
        # creature's chat stream. We delegate the iterator object
        # directly rather than wrapping it in another async generator
        # so cancellation semantics match exactly.
        creature = self._engine.get_creature(creature_id)
        return creature.chat(message)

    # === Per-creature control ===

    async def interrupt(self, creature_id: str) -> None:
        agent = self._engine.get_creature(creature_id).agent
        agent.interrupt()

    async def list_jobs(self, creature_id: str) -> list[dict[str, Any]]:
        agent = self._engine.get_creature(creature_id).agent
        jobs = [j.to_dict() for j in agent.executor.get_running_jobs()]
        jobs.extend(j.to_dict() for j in agent.subagent_manager.get_running_jobs())
        return jobs

    async def stop_job(self, creature_id: str, job_id: str) -> bool:
        agent = self._engine.get_creature(creature_id).agent
        if agent._interrupt_direct_job(job_id):
            return True
        if await agent.executor.cancel(job_id):
            return True
        return await agent.subagent_manager.cancel(job_id)

    async def promote_job(self, creature_id: str, job_id: str) -> bool:
        agent = self._engine.get_creature(creature_id).agent
        return bool(agent._promote_handle(job_id))

    # ------------------------------------------------------------------
    # Per-creature ops — delegate to ``terrarium.creature_ops``.
    # That module lives in the same tier so no studio import is needed.
    # ------------------------------------------------------------------

    def _agent(self, creature_id: str):
        return self._engine.get_creature(creature_id).agent

    async def chat_history(self, creature_id: str) -> dict[str, Any]:
        return _chat_history_for(self._engine, creature_id)

    async def chat_branches(self, creature_id: str) -> list[dict[str, Any]]:
        return _chat_branches_for(self._engine, creature_id)

    async def regenerate(
        self,
        creature_id: str,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        agent = self._agent(creature_id)
        await agent.regenerate_last_response(
            turn_index=turn_index, branch_view=branch_view
        )
        return {"status": "regenerating"}

    async def edit_message(
        self,
        creature_id: str,
        msg_idx: int,
        content: str | list[dict[str, Any]],
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> bool:
        agent = self._agent(creature_id)
        return await agent.edit_and_rerun(
            msg_idx,
            content,
            turn_index=turn_index,
            user_position=user_position,
            branch_view=branch_view,
        )

    async def rewind(self, creature_id: str, msg_idx: int) -> None:
        await self._agent(creature_id).rewind_to(msg_idx)

    async def get_scratchpad(self, creature_id: str) -> dict[str, str]:
        return _agent_scratchpad(self._agent(creature_id))

    async def patch_scratchpad(
        self,
        creature_id: str,
        updates: dict[str, str | None],
    ) -> dict[str, str]:
        return _agent_patch_scratchpad(self._agent(creature_id), updates)

    async def list_triggers(self, creature_id: str) -> list[dict[str, Any]]:
        return _agent_triggers(self._agent(creature_id))

    async def get_env(self, creature_id: str) -> dict[str, Any]:
        return _agent_env(self._agent(creature_id))

    async def get_system_prompt(self, creature_id: str) -> dict[str, str]:
        return _agent_system_prompt(self._agent(creature_id))

    async def get_working_dir(self, creature_id: str) -> str:
        return _agent_working_dir(self._agent(creature_id))

    async def set_working_dir(self, creature_id: str, new_path: str) -> str:
        return _agent_set_working_dir(self._agent(creature_id), new_path)

    async def native_tool_inventory(self, creature_id: str) -> list[dict[str, Any]]:
        return _agent_native_tool_inventory(self._agent(creature_id))

    async def get_native_tool_options(
        self, creature_id: str
    ) -> dict[str, dict[str, Any]]:
        return _agent_get_native_tool_options(self._agent(creature_id))

    async def set_native_tool_options(
        self,
        creature_id: str,
        tool: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        return _agent_set_native_tool_options(self._agent(creature_id), tool, values)

    async def switch_model(self, creature_id: str, model: str) -> str:
        agent = self._agent(creature_id)
        if hasattr(agent, "switch_model"):
            agent.switch_model(model)
        else:
            agent.config.model = model
        return model

    async def list_plugins(self, creature_id: str) -> list[dict[str, Any]]:
        return _agent_list_plugins(self._agent(creature_id))

    async def toggle_plugin(
        self,
        creature_id: str,
        plugin_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        result = await _agent_toggle_plugin(
            self._agent(creature_id), plugin_name, enabled
        )
        return {"plugin": result["name"], "enabled": result["enabled"]}

    # ------------------------------------------------------------------
    # Module catalog + command execution — pure agent-touch via
    # ``creature_ops``.  Same routing pattern as the per-creature ops.
    # ------------------------------------------------------------------

    async def list_modules(self, creature_id: str) -> list[dict[str, Any]]:
        return _agent_list_modules(self._agent(creature_id))

    async def get_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        return _agent_get_module_options(
            self._agent(creature_id), module_type, module_name
        )

    async def set_module_options(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        return _agent_set_module_options(
            self._agent(creature_id), module_type, module_name, values
        )

    async def toggle_module(
        self,
        creature_id: str,
        module_type: str,
        module_name: str,
    ) -> dict[str, Any]:
        return await _agent_toggle_module(
            self._agent(creature_id), module_type, module_name
        )

    async def execute_command(
        self,
        creature_id: str,
        command: str,
        args: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await _agent_execute_command(
            self._agent(creature_id), command, _normalize_command_args(args)
        )

    async def wire_creature(
        self,
        graph_id: str,
        creature_id: str,
        channel: str,
        direction: str,
        *,
        enabled: bool = True,
    ) -> None:
        _wire_creature_on_engine(
            self._engine,
            graph_id,
            creature_id,
            channel,
            direction,
            enabled=enabled,
        )

    async def list_output_wiring(self, creature_id: str) -> list[dict[str, Any]]:
        try:
            edges = self._engine.list_output_wiring(creature_id)
        except Exception:
            edges = []
        return [dict(e) for e in edges]

    async def wire_output(
        self,
        creature_id: str,
        target: str | dict[str, Any],
    ) -> dict[str, Any]:
        edge_id = await self._engine.wire_output(creature_id, target)
        return {"edge_id": str(edge_id)}

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool:
        return bool(await self._engine.unwire_output(creature_id, edge_id))

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool:
        return bool(await self._engine.unwire_output_sink(creature_id, sink_id))

    async def attach_policies(self, creature_id: str) -> list[str]:
        return _attach_policies_for(self._engine, creature_id)

    async def session_attach_policies(self, session_id: str) -> list[str]:
        return _session_attach_policies_for(self._engine, session_id)

    async def runtime_graph_snapshot(self) -> dict[str, Any]:
        # ``meta_lookup`` (graph_id -> dict of name/kind/created_at/...)
        # is installed by the API layer at boot via
        # :meth:`set_runtime_graph_meta_lookup` so the terrarium tier
        # doesn't import studio.  Absent on remote services and tests
        # — the snapshot still works with default fields.
        meta_fn = getattr(self, "_meta_lookup", None)
        snap = _build_runtime_graph_snapshot_for(self._engine, meta_lookup=meta_fn)
        for graph in snap.get("graphs", []):
            graph.setdefault("node_id", self._node_id)
        return snap

    def set_runtime_graph_meta_lookup(self, fn) -> None:
        """Install a ``(graph_id) -> meta_dict`` callable used by
        :meth:`runtime_graph_snapshot` to enrich each graph with the
        studio-tier session metadata (name/kind/created_at).  Wired
        from ``api/app.py`` boot so the snapshot stays tier-clean.
        Idempotent — overwrites whatever was installed previously.
        """
        self._meta_lookup = fn

    # === Events ===

    def subscribe(
        self,
        filter: EventFilter | None = None,
    ) -> AsyncIterator[EngineEvent]:
        return self._engine.subscribe(filter)


__all__ = [
    "CreatureInfo",
    "LocalTerrariumService",
    "TerrariumService",
    "creature_to_info",
]
