"""Runtime group-prompt — keeps each creature's system prompt in sync
with the live wiring it has access to.

Replaces ``studio.sessions.runtime_topology``. The block lives at the
Terrarium layer because engine-driven mutations (tool calls, recipe
applies, hot-plug API operations) all flow through one engine — keeping
the refresh listener engine-side guarantees no path forgets to update
the prompt.

Sentinel-bounded by ``<!-- runtime-graph -->`` ... ``<!-- /runtime-graph -->``
so repeated refreshes replace the previous block instead of stacking.

Block content per design.md Section 8:

- caller name + creature_id (``— privileged`` suffix when applicable)
- listen channels (one bullet per channel + description)
- send channels (with ``use send_channel`` reminder)
- output wires (inbound from / outbound to)
- spawned children not yet in caller's graph (privileged only)
- closing reminder line + arrival-tag legend so the model can
  disambiguate ``[Channel '...' from X]`` / ``[output-wire from X]`` /
  ``[direct from X]``
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.events import EventKind
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.creature_host import Creature
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)

_BEGIN = "<!-- runtime-graph -->"
_END = "<!-- /runtime-graph -->"

_REFRESH_DEBOUNCE_SEC = 0.1
_REFRESH_KINDS = {
    EventKind.CREATURE_STARTED,
    EventKind.CREATURE_STOPPED,
    EventKind.TOPOLOGY_CHANGED,
    EventKind.OUTPUT_WIRE_ADDED,
    EventKind.OUTPUT_WIRE_REMOVED,
    EventKind.PARENT_LINK_CHANGED,
}


class RuntimeGraphPrompt:
    """Subscribes to engine events and refreshes every affected
    creature's runtime-graph system-prompt block.
    """

    def __init__(self, engine: "Terrarium") -> None:
        self._engine = engine
        self._task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Handle] = {}
        self._attached = False

    def attach(self) -> None:
        """Start the engine-event listener loop. Idempotent."""
        if self._attached:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._attached = True
        self._task = loop.create_task(self._run())

    def detach(self) -> None:
        """Stop the listener and cancel pending refreshes."""
        self._attached = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None
        for handle in list(self._pending.values()):
            handle.cancel()
        self._pending.clear()

    async def _run(self) -> None:
        try:
            async for ev in self._engine.subscribe():
                if not self._attached:
                    break
                if ev.kind not in _REFRESH_KINDS:
                    continue
                self._schedule_refresh_for_event(ev)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("RuntimeGraphPrompt listener crashed", error=str(exc))

    def _schedule_refresh_for_event(self, ev: Any) -> None:
        target_ids: set[str] = set()
        if ev.creature_id:
            target_ids.add(ev.creature_id)
        if ev.graph_id:
            graph = self._engine._topology.graphs.get(ev.graph_id)
            if graph is not None:
                target_ids.update(graph.creature_ids)
        if ev.kind == EventKind.TOPOLOGY_CHANGED:
            payload = ev.payload or {}
            for gid in list(payload.get("old_graph_ids", [])) + list(
                payload.get("new_graph_ids", [])
            ):
                graph = self._engine._topology.graphs.get(gid)
                if graph is not None:
                    target_ids.update(graph.creature_ids)
            target_ids.update(payload.get("affected", []))
        if ev.kind == EventKind.PARENT_LINK_CHANGED:
            # Spawned children sit in their own singleton graph until
            # wired, so the parent's graph won't show them via the
            # graph-id lookup above. Pull the parent in explicitly so
            # its ``Spawned (not yet wired):`` section refreshes.
            payload = ev.payload or {}
            parent = payload.get("parent")
            if parent:
                target_ids.add(parent)
        for cid in target_ids:
            self._schedule_refresh(cid)

    def _schedule_refresh(self, creature_id: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        existing = self._pending.pop(creature_id, None)
        if existing is not None:
            existing.cancel()
        handle = loop.call_later(
            _REFRESH_DEBOUNCE_SEC,
            self._do_refresh,
            creature_id,
        )
        self._pending[creature_id] = handle

    def _do_refresh(self, creature_id: str) -> None:
        self._pending.pop(creature_id, None)
        creature = self._engine._creatures.get(creature_id)
        if creature is None:
            return
        try:
            section = build_runtime_graph_section(self._engine, creature)
            apply_managed_section(creature.agent, section)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "runtime-graph refresh failed",
                creature_id=creature_id,
                error=str(exc),
            )

    async def refresh_creature(self, creature: "Creature") -> None:
        """Refresh the runtime-graph block for ``creature`` immediately.

        Bypasses the 100 ms debounce — used by tests and explicit prompt
        recomputation. ``async`` so callers can ``await`` consistently
        even though the body is synchronous today.
        """
        section = build_runtime_graph_section(self._engine, creature)
        apply_managed_section(creature.agent, section)


# ---------------------------------------------------------------------------
# block builder
# ---------------------------------------------------------------------------


def build_runtime_graph_section(engine: "Terrarium", creature: "Creature") -> str:
    """Render the runtime-graph block for ``creature``."""
    graph = engine._topology.graphs.get(creature.graph_id)
    if graph is None:
        return ""

    listens = sorted(creature.listen_channels)
    sends = sorted(creature.send_channels)

    output_in: list[str] = []
    output_out: list[str] = []
    self_id = getattr(creature.agent, "_creature_id", creature.creature_id)
    for other_cid, other_creature in engine._creatures.items():
        agent_cfg = getattr(other_creature.agent, "config", None)
        wiring_entries = (
            getattr(agent_cfg, "output_wiring", None) if agent_cfg else None
        ) or []
        for entry in wiring_entries:
            target = getattr(entry, "to", "")
            if target == self_id or target == creature.name:
                if other_cid != creature.creature_id:
                    output_in.append(other_creature.name)
    own_cfg = getattr(creature.agent, "config", None)
    own_entries = (getattr(own_cfg, "output_wiring", None) if own_cfg else None) or []
    for entry in own_entries:
        target = getattr(entry, "to", "")
        if target:
            output_out.append(target)

    spawned: list[tuple[str, str]] = []
    if creature.is_privileged:
        for c in engine._creatures.values():
            if (
                getattr(c, "parent_creature_id", None) == creature.creature_id
                and c.graph_id != creature.graph_id
            ):
                cfg_name = getattr(getattr(c, "config", None), "name", "")
                spawned.append((c.name, cfg_name or c.creature_id))

    if not (
        listens or sends or output_in or output_out or spawned or creature.is_privileged
    ):
        return ""

    privileged_suffix = " — privileged" if creature.is_privileged else ""

    lines = [
        "## Live Group (auto-managed)",
        "",
        f"You are `{creature.creature_id}` ({creature.name}){privileged_suffix}.",
        "",
    ]
    if listens:
        lines.append("Listening to:")
        for n in listens:
            desc = graph.channels[n].description if n in graph.channels else ""
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- `{n}`{suffix}")
        lines.append("")
    if sends:
        lines.append("Sending to (use `send_channel`):")
        for n in sends:
            desc = graph.channels[n].description if n in graph.channels else ""
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- `{n}`{suffix}")
        lines.append("")
    if output_in or output_out:
        lines.append("Output wires:")
        if output_in:
            lines.append(f"- inbound from: {', '.join(sorted(set(output_in)))}")
        if output_out:
            lines.append(f"- outbound to: {', '.join(sorted(set(output_out)))}")
        lines.append("")
    if spawned:
        lines.append("Spawned (not yet wired):")
        for name, cfg_ref in spawned:
            lines.append(f"- `{name}` ({cfg_ref})")
        lines.append("")
    lines.append(
        "For channels above use `send_channel`. For one-shot point-to-point "
        "messaging use `group_send`. Do not use `send_message` for graph traffic."
    )
    lines.append(
        "Output-wire arrivals are tagged `[output-wire from <source>]`; "
        "direct sends are tagged `[direct from <source>]`; channel messages "
        "are tagged `[Channel '<name>' from <sender>]`."
    )
    return "\n".join(lines).rstrip()


def apply_managed_section(agent: Any, content: str) -> None:
    """Splice ``content`` into the agent's system prompt, replacing any
    prior runtime-graph block. Silently no-ops when the agent's prompt
    surface isn't reachable (test fakes)."""
    controller = getattr(agent, "controller", None)
    conversation = getattr(controller, "conversation", None) if controller else None
    get_system = (
        getattr(conversation, "get_system_message", None) if conversation else None
    )
    if not callable(get_system):
        return
    sys_msg = get_system()
    if sys_msg is None or not isinstance(sys_msg.content, str):
        return
    current = _strip_existing_block(sys_msg.content)
    if not content:
        sys_msg.content = current.rstrip()
        return
    block = f"\n\n{_BEGIN}\n{content}\n{_END}"
    sys_msg.content = current.rstrip() + block


def _strip_existing_block(text: str) -> str:
    start = text.find(_BEGIN)
    if start < 0:
        return text
    end = text.find(_END, start)
    if end < 0:
        return text
    end += len(_END)
    head = text[:start]
    tail = text[end:]
    return head.rstrip() + ("\n" if tail.lstrip() else "") + tail.lstrip()
