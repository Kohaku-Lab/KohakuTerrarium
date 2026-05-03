"""Engine-backed topology operations — channels + connect/disconnect.

Replaces ``KohakuManager.terrarium_channel_add / channel_list /
channel_info / channel_send / channel_stream`` and ``creature_wire /
creature_channel_*`` and ``agent_channel_*``.

Channels live inside a graph (== session). ``connect`` / ``disconnect``
operate at the engine layer and may merge / split graphs as a side
effect (the engine handles topology bookkeeping).
"""

from typing import Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.studio.sessions.runtime_topology import (
    refresh_creature_topology_prompt,
    refresh_graph_topology_prompts,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.topology import ChannelKind
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_kind(kind: str) -> ChannelKind:
    return ChannelKind.BROADCAST if kind == "broadcast" else ChannelKind.QUEUE


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


async def add_channel(
    engine: Terrarium,
    session_id: str,
    name: str,
    *,
    channel_type: str = "queue",
    description: str = "",
) -> dict[str, Any]:
    """Declare a channel in a session."""
    info = await engine.add_channel(
        session_id, name, kind=_resolve_kind(channel_type), description=description
    )
    # Surface the new channel to every creature in the graph so the
    # next time one of them wants to send, the prompt already lists
    # the channel as visible.
    refresh_graph_topology_prompts(engine, session_id)
    return {
        "name": info.name,
        "type": info.kind.value if hasattr(info.kind, "value") else str(info.kind),
        "description": info.description,
    }


def list_channels(engine: Terrarium, session_id: str) -> list[dict[str, Any]]:
    """List shared channels in a session."""
    env = engine._environments.get(session_id)
    if env is None:
        raise KeyError(f"session {session_id!r} not found")
    return env.shared_channels.get_channel_info()


def channel_info(
    engine: Terrarium, session_id: str, channel: str
) -> dict[str, Any] | None:
    """Get info about a specific channel in a session."""
    env = engine._environments.get(session_id)
    if env is None:
        raise KeyError(f"session {session_id!r} not found")
    ch = env.shared_channels.get(channel)
    if ch is None:
        return None
    return {
        "name": ch.name,
        "type": ch.channel_type,
        "description": ch.description,
        "qsize": ch.qsize,
        "scope": "shared",
    }


async def send_to_channel(
    engine: Terrarium,
    session_id: str,
    channel: str,
    content: str | list[dict],
    sender: str = "human",
) -> str:
    """Send a message to a session channel.  Returns ``message_id``."""
    env = engine._environments.get(session_id)
    if env is None:
        raise KeyError(f"session {session_id!r} not found")
    ch = env.shared_channels.get(channel)
    if ch is None:
        available = env.shared_channels.list_channels()
        raise ValueError(f"Channel '{channel}' not found. Available: {available}")
    msg = ChannelMessage(sender=sender, content=content)
    await ch.send(msg)
    return msg.message_id


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------


async def connect(
    engine: Terrarium,
    sender: str,
    receiver: str,
    *,
    channel: str | None = None,
    channel_type: str = "queue",
) -> dict[str, Any]:
    """Wire ``sender → receiver`` via a channel.  Returns the engine
    ``ConnectionResult`` as a dict.
    """
    result = await engine.connect(
        sender, receiver, channel=channel, kind=_resolve_kind(channel_type)
    )
    return _connection_result_to_dict(result)


async def disconnect(
    engine: Terrarium,
    sender: str,
    receiver: str,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """Drop the ``sender → receiver`` link.  Returns the engine
    ``DisconnectionResult`` as a dict.
    """
    result = await engine.disconnect(sender, receiver, channel=channel)
    return _disconnection_result_to_dict(result)


# ---------------------------------------------------------------------------
# Hot-plug per-creature wire (legacy ``creature_wire`` parity)
# ---------------------------------------------------------------------------


async def wire_creature(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    channel: str,
    direction: str,
    *,
    enabled: bool = True,
) -> None:
    """Toggle a listen / send edge for a creature on an existing channel.

    ``direction`` is ``"listen"`` or ``"send"``.  When ``creature_id``
    is the literal ``"root"`` the call resolves to the session's root
    creature (if any).  This mirrors the legacy
    ``KohakuManager.creature_wire`` body — it only updates topology
    edges; channel-trigger injection is the engine's responsibility.
    """
    if creature_id == "root":
        graph = engine.get_graph(session_id)
        for cid in graph.creature_ids:
            try:
                c = engine.get_creature(cid)
            except KeyError:
                continue
            if getattr(c, "is_root", False):
                creature_id = cid
                break
        else:
            raise KeyError(f"session {session_id!r} has no root creature")

    graph = engine.get_graph(session_id)
    if creature_id not in graph.creature_ids:
        raise KeyError(f"creature {creature_id!r} not in session {session_id!r}")
    creature = engine.get_creature(creature_id)
    if channel not in graph.channels:
        raise KeyError(f"channel {channel!r} not in session {session_id!r}")
    if direction == "listen":
        _topo.set_listen(engine._topology, creature_id, channel, listening=enabled)
        if enabled:
            env = engine._environments.get(session_id)
            registry = (
                getattr(env, "shared_channels", None) if env is not None else None
            )
            if registry is None:
                raise KeyError(f"session {session_id!r} has no shared channel registry")
            _channels.register_channel_in_environment(registry, graph.channels[channel])
            _channels.inject_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=channel,
                registry=registry,
                ignore_sender=creature.name,
            )
            if channel not in creature.listen_channels:
                creature.listen_channels.append(channel)
        else:
            _channels.remove_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=channel,
            )
            if channel in creature.listen_channels:
                creature.listen_channels.remove(channel)
    elif direction == "send":
        _topo.set_send(engine._topology, creature_id, channel, sending=enabled)
        if enabled and channel not in creature.send_channels:
            creature.send_channels.append(channel)
        elif not enabled and channel in creature.send_channels:
            creature.send_channels.remove(channel)
    else:
        raise ValueError(f"direction must be 'listen' or 'send', got {direction!r}")

    # Keep the agent's system prompt aligned with the live wiring.
    # Without this the LLM keeps inventing channel names because
    # nothing in its context tells it which channels actually exist
    # (cf. confabulated ``report_to_root`` on solo creatures).
    refresh_creature_topology_prompt(engine, creature_id)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _connection_result_to_dict(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"channel": getattr(result, "channel", "")}
    delta = getattr(result, "delta", None)
    if delta is not None:
        out["delta"] = {
            "kind": getattr(delta, "kind", "nothing"),
            "old_graph_ids": list(getattr(delta, "old_graph_ids", []) or []),
            "new_graph_ids": list(getattr(delta, "new_graph_ids", []) or []),
            "affected": sorted(getattr(delta, "affected_creatures", []) or []),
        }
    elif hasattr(result, "delta_kind"):
        out["delta"] = {"kind": getattr(result, "delta_kind", "nothing")}
    out["graph_id"] = getattr(result, "graph_id", "")
    return out


def _disconnection_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "removed_channel": getattr(result, "removed_channel", None),
        "delta": {
            "kind": getattr(getattr(result, "delta", None), "kind", "nothing"),
            "old_graph_ids": list(
                getattr(getattr(result, "delta", None), "old_graph_ids", []) or []
            ),
            "new_graph_ids": list(
                getattr(getattr(result, "delta", None), "new_graph_ids", []) or []
            ),
            "affected": sorted(
                getattr(getattr(result, "delta", None), "affected_creatures", []) or []
            ),
        },
    }
