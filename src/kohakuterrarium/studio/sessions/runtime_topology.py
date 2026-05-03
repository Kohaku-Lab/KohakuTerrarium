"""Runtime topology prompt — keeps an agent's system prompt in sync
with the live channel wiring.

The static :func:`build_channel_topology_prompt` in
``terrarium.config`` is built once at recipe-load time from a
``TerrariumConfig``.  It does not run for solo-creature sessions, and
it does not refresh when the user wires a creature to a new channel
at runtime.  That left agents without any awareness of the channels
they were supposed to use — they confabulated names like
``report_to_root`` because the prompt simply didn't tell them what
existed.

This module rebuilds a topology section from live engine state and
applies it to ``agent.controller.conversation`` system message,
enclosed in a sentinel block so we can replace prior versions on
every refresh instead of accumulating duplicates.
"""

from typing import Iterable

from kohakuterrarium.terrarium.engine import Terrarium

# Sentinel that lets us identify the auto-managed runtime topology
# block when the prompt has had multiple managed sections appended
# over a creature's lifetime.
_BEGIN = "<!-- runtime-topology -->"
_END = "<!-- /runtime-topology -->"


def refresh_creature_topology_prompt(engine: Terrarium, creature_id: str) -> str | None:
    """Rebuild the runtime-topology section for ``creature_id`` and
    splice it into the agent's system prompt.  Returns the section
    text (or ``None`` if the creature can't be resolved)."""
    try:
        creature = engine.get_creature(creature_id)
    except KeyError:
        return None
    graph = engine._topology.graphs.get(creature.graph_id)
    if graph is None:
        return None

    listens = sorted(creature.listen_channels)
    sends = sorted(creature.send_channels)
    other = [
        name for name in graph.channels if name not in listens and name not in sends
    ]
    section = _build_section(
        creature_name=creature.name,
        listens=listens,
        sends=sends,
        other=sorted(other),
        channel_kinds={
            name: getattr(info.kind, "value", str(info.kind))
            for name, info in graph.channels.items()
        },
    )
    _apply_managed_section(creature.agent, section)
    return section


def refresh_graph_topology_prompts(engine: Terrarium, graph_id: str) -> None:
    """Refresh every creature in a graph.  Used after a channel is
    added so each creature sees the new "other channels" entry without
    waiting for its own next wire."""
    graph = engine._topology.graphs.get(graph_id)
    if graph is None:
        return
    for cid in list(graph.creature_ids):
        refresh_creature_topology_prompt(engine, cid)


def _build_section(
    *,
    creature_name: str,
    listens: list[str],
    sends: list[str],
    other: list[str],
    channel_kinds: dict[str, str],
) -> str:
    """Return the topology prompt body.  Empty string if there are no
    channels at all — the surrounding sentinel still gets written so a
    later wire that *does* add channels can replace it cleanly."""
    if not listens and not sends and not other:
        return ""

    def _line(name: str) -> str:
        kind = channel_kinds.get(name, "queue")
        return f"- `{name}` ({kind})"

    lines = [
        "## Team Channels (live)",
        "",
        "These are the only channels that exist for you right now.  The",
        "list is rebuilt every time the user wires you up, so trust it.",
        "Do **not** invent channel names — `send_message` will reject any",
        "name not in this list.",
        "",
    ]
    if listens:
        lines.append("**Incoming (you receive on these):**")
        lines.extend(_line(n) for n in listens)
        lines.append("")
    if sends:
        lines.append("**Outgoing (use `send_message` to write here):**")
        lines.extend(_line(n) for n in sends)
        lines.append("")
    if other:
        lines.append(
            "**Other channels in this molecule** (visible but you're not "
            "wired to them — leave alone unless instructed):"
        )
        lines.extend(_line(n) for n in other)
        lines.append("")
    lines.append(
        f"Direct messages to you arrive on the implicit `{creature_name}` channel."
    )
    return "\n".join(lines).rstrip()


def _apply_managed_section(agent, content: str) -> None:
    # Defensive: test fakes / older agent stand-ins may not expose the
    # controller/conversation chain. Silently skip when the prompt
    # surface isn't reachable rather than failing the wiring call.
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
    # Collapse the leading whitespace introduced by our previous insert
    # so repeated refreshes don't grow blank lines.
    return head.rstrip() + ("\n" if tail.lstrip() else "") + tail.lstrip()


def channel_listing_for(engine: Terrarium, creature_id: str) -> dict[str, list[str]]:
    """Helper used by tools that want to render the current channel set
    in error messages — keeps the wording consistent with the prompt."""
    try:
        creature = engine.get_creature(creature_id)
    except KeyError:
        return {"listen": [], "send": [], "other": []}
    graph = engine._topology.graphs.get(creature.graph_id)
    if graph is None:
        return {"listen": [], "send": [], "other": []}
    listens = list(creature.listen_channels)
    sends = list(creature.send_channels)
    other = [n for n in graph.channels if n not in listens and n not in sends]
    return {"listen": sorted(listens), "send": sorted(sends), "other": sorted(other)}


def _coerce_iterable(value) -> Iterable[str]:
    return value if value else ()
