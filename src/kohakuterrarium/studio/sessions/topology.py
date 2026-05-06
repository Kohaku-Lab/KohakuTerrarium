"""Engine-backed topology operations — channels + connect/disconnect.

Channels live inside a graph (== session). ``connect`` / ``disconnect``
operate at the engine layer and may merge / split graphs as a side
effect (the engine handles topology bookkeeping). Graph topology
channels are always broadcast — channel-kind variants are sub-agent
private comms only and live in :mod:`core.channel`.
"""

from typing import Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


async def add_channel(
    engine: Terrarium,
    session_id: str,
    name: str,
    *,
    channel_type: str = "broadcast",
    description: str = "",
) -> dict[str, Any]:
    """Declare a channel in a session.

    ``channel_type`` is accepted for legacy HTTP payload compatibility;
    graph channels are always broadcast at the Terrarium layer.
    """
    _ = channel_type
    info = await engine.add_channel(session_id, name, description=description)
    return {
        "name": info.name,
        "type": "broadcast",
        "description": info.description,
    }


async def remove_channel(
    engine: Terrarium,
    session_id: str,
    name: str,
) -> dict[str, Any]:
    """Remove a channel from a session, returning the topology delta."""
    delta = await engine.remove_channel(session_id, name)
    return {
        "removed": name,
        "delta": {
            "kind": delta.kind,
            "old_graph_ids": list(delta.old_graph_ids),
            "new_graph_ids": list(delta.new_graph_ids),
            "affected": sorted(delta.affected_creatures),
        },
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
    channel_type: str = "broadcast",
) -> dict[str, Any]:
    """Wire ``sender → receiver`` via a channel.  Returns the engine
    ``ConnectionResult`` as a dict.

    ``channel_type`` is accepted for legacy HTTP payload compatibility;
    graph channels are always broadcast at the Terrarium layer.
    """
    _ = channel_type
    result = await engine.connect(sender, receiver, channel=channel)
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
# Hot-plug per-creature wire
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
    is the literal ``"root"`` the call resolves to the session's
    privileged creature (if any).  Updates topology edges and injects /
    removes the channel trigger as needed.
    """
    if creature_id == "root":
        # Disambiguation when multiple privileged creatures share a
        # graph: prefer ``creature_id == "root"`` (recipe convention),
        # then ``name == "root"``, then first sorted privileged id.
        graph = engine.get_graph(session_id)
        privileged: list = []
        for cid in sorted(graph.creature_ids):
            try:
                c = engine.get_creature(cid)
            except KeyError:
                continue
            if getattr(c, "is_privileged", False):
                privileged.append(c)
        if not privileged:
            raise KeyError(f"session {session_id!r} has no privileged creature")
        chosen = (
            next(
                (c for c in privileged if c.creature_id == "root"),
                None,
            )
            or next(
                (c for c in privileged if c.name == "root"),
                None,
            )
            or privileged[0]
        )
        creature_id = chosen.creature_id

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

    # Emit a topology event so engine subscribers (notably the runtime
    # graph prompt block) refresh affected creatures' system prompts.
    # ``wire_creature`` doesn't change graph membership, so the delta
    # is "nothing" — but the prompt content (listen/send lists) just
    # changed, and that's what listeners care about.
    engine._emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            creature_id=creature_id,
            graph_id=session_id,
            payload={
                "kind": "nothing",
                "old_graph_ids": [session_id],
                "new_graph_ids": [session_id],
                "affected": [creature_id],
            },
        )
    )


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
    """Serialize a :class:`DisconnectionResult` for the HTTP route.

    The dataclass exposes ``channels`` (the unwired channel names) and
    ``delta_kind``. The full ``TopologyDelta`` (with old/new graph ids
    and affected-creatures sets) is not surfaced on the result today;
    callers that need it should subscribe to the ``TOPOLOGY_CHANGED``
    engine event instead.
    """
    return {
        "channels": list(getattr(result, "channels", []) or []),
        "delta": {"kind": getattr(result, "delta_kind", "nothing")},
    }
