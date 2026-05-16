"""Engine-backed topology operations — channels + connect/disconnect.

Channels live inside a graph (== session). ``connect`` / ``disconnect``
operate at the engine layer and may merge / split graphs as a side
effect (the engine handles topology bookkeeping). Graph topology
channels are always broadcast — channel-kind variants are sub-agent
private comms only and live in :mod:`core.channel`.
"""

from typing import Any

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


async def add_channel(
    service: "TerrariumService",
    session_id: str,
    name: str,
    *,
    channel_type: str = "broadcast",
    description: str = "",
) -> dict[str, Any]:
    """Declare a channel in a session.

    Routes through the service Protocol so multi-node deployments
    reach the graph's home node.  Unwrapping to the local engine
    here silently 404s for remote-hosted graphs.

    ``channel_type`` is accepted for legacy HTTP payload compatibility;
    graph channels are always broadcast at the Terrarium layer.
    """
    _ = channel_type
    info = await service.add_channel(session_id, name, description=description)
    return {
        "name": info.name,
        "type": "broadcast",
        "description": getattr(info, "description", description),
    }


async def remove_channel(
    service: "TerrariumService",
    session_id: str,
    name: str,
) -> dict[str, Any]:
    """Remove a channel from a session, returning the topology delta.

    Service-routed: works for remote-hosted graphs.
    """
    delta = await service.remove_channel(session_id, name)
    return {
        "removed": name,
        "delta": {
            "kind": getattr(delta, "kind", "nothing"),
            "old_graph_ids": list(getattr(delta, "old_graph_ids", []) or []),
            "new_graph_ids": list(getattr(delta, "new_graph_ids", []) or []),
            "affected": sorted(getattr(delta, "affected_creatures", []) or []),
        },
    }


async def list_channels(
    service: "TerrariumService", session_id: str
) -> list[dict[str, Any]]:
    """List shared channels in a session.

    Routes through the :class:`TerrariumService` Protocol — a worker
    session's channels live on the worker, NOT the host engine, so an
    ``as_engine`` reach-in would 404 every worker session even right
    after a successful ``add_channel``.
    """
    channels = await service.list_channels(session_id)
    return [
        {
            "name": c.name,
            "type": "broadcast",
            "description": c.description,
            "scope": "shared",
        }
        for c in channels
    ]


async def channel_info(
    service: "TerrariumService", session_id: str, channel: str
) -> dict[str, Any] | None:
    """Get info about a specific channel in a session, including history.

    Service-routed for the same reason as :func:`list_channels`.  The
    response includes a ``history`` list of recorded messages on the
    channel — the frontend uses this as the source of truth for the
    cross-creature broadcast log (see journey bug #134 — the chat WS
    only attaches AFTER messages were sent, so the WS replay alone
    misses them).  When the channel object exists but has no recorded
    history yet (or the service can't reach it — defensive), ``history``
    is an empty list rather than absent so the JSON shape stays stable.
    """
    target: Any = None
    for c in await service.list_channels(session_id):
        if c.name == channel:
            target = c
            break
    if target is None:
        return None
    # Best effort: pull history from the service.  A service that
    # doesn't host the channel raises KeyError; surface that to the
    # caller so the HTTP layer can decide the right status code.  For
    # everything else (e.g. a backend that returns "channel exists but
    # history unsupported"), degrade to an empty list.
    history: list[dict[str, Any]] = []
    try:
        history = await service.channel_history(session_id, channel)
    except KeyError:
        history = []
    except Exception:  # pragma: no cover - defensive
        logger.debug("channel_history failed", session_id=session_id, channel=channel)
        history = []
    return {
        "name": target.name,
        "type": "broadcast",
        "description": target.description,
        "scope": "shared",
        "history": history,
    }


async def send_to_channel(
    service: "TerrariumService",
    session_id: str,
    channel: str,
    content: str | list[dict],
    sender: str = "human",
) -> str:
    """Send a message to a session channel.  Returns ``message_id``.

    Routes through the service Protocol so lab-host mode reaches the
    worker that hosts the channel object — the legacy host-engine
    short-circuit returned 404 for every cross-node channel because the
    host's coordination engine never held the channel itself.  The
    single-host path is identical: ``LocalTerrariumService`` forwards
    to its local engine.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        # Lab-host mode — go through the service so the call lands on
        # the graph's home worker.
        return await service.send_channel_message(
            session_id, channel, content, sender=sender
        )
    # CF-3 — host engine present but the channel may not live on it.
    # Two scenarios force a service-routed send instead of the direct
    # engine path:
    #
    # 1. ``session_id`` is a cluster member (recorded in
    #    ``service._cluster_links``).  Cluster channels are replicated
    #    on worker engines, not on the host's coordination engine; the
    #    legacy engine reach-in would 404 every cluster-side send.
    # 2. The channel does not exist in the host engine's environments.
    #    Defensive fallback for any future code path where the host
    #    engine is exposed but the session lives elsewhere — without
    #    this, the unconditional engine walk surfaces a ``KeyError``
    #    even though ``service.send_channel_message`` could route to
    #    the worker that actually owns the channel.
    cluster_map = cluster_fold.sid_to_primary(service)
    env = engine._environments.get(session_id)
    channel_obj = env.shared_channels.get(channel) if env is not None else None
    if session_id in cluster_map or env is None or channel_obj is None:
        send_fn = getattr(service, "send_channel_message", None)
        if send_fn is not None:
            return await send_fn(session_id, channel, content, sender=sender)
    # Single-host / local mode — keep the direct engine path so the
    # behaviour is identical to before (in particular, ``KeyError``
    # surfaces with the session_id-shaped message the existing HTTP
    # route maps to 404).  Unit tests pass a raw engine in for
    # ``service`` here; the direct path keeps them green without
    # requiring them to add a ``send_channel_message`` method.
    if env is None:
        raise KeyError(f"session {session_id!r} not found")
    if channel_obj is None:
        available = env.shared_channels.list_channels()
        raise ValueError(f"Channel '{channel}' not found. Available: {available}")
    msg = ChannelMessage(sender=sender, content=content)
    await channel_obj.send(msg)
    return msg.message_id


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------


async def connect(
    service: "TerrariumService",
    sender: str,
    receiver: str,
    *,
    channel: str | None = None,
    channel_type: str = "broadcast",
) -> dict[str, Any]:
    """Wire ``sender → receiver`` via a channel.  Returns the
    ``ConnectionResult`` as a dict.

    Routes through the service Protocol so cross-node connect
    actually fires the ``MultiNodeTerrariumService.connect`` cross-
    site path (channel replicated on both nodes + terrarium.broadcast
    cross-subscription).  Unwrapping to the local engine here makes
    cross-cluster wiring silently impossible from the graph editor.

    ``channel_type`` is accepted for legacy HTTP payload compatibility;
    graph channels are always broadcast at the Terrarium layer.
    """
    _ = channel_type
    result = await service.connect(sender, receiver, channel=channel)
    return _connection_result_to_dict(result)


async def disconnect(
    service: "TerrariumService",
    sender: str,
    receiver: str,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """Drop the ``sender → receiver`` link.  Returns the
    ``DisconnectionResult`` as a dict.

    Service-routed: handles cross-node creatures by undoing the
    forwarder subscription on top of per-side wire removal.
    """
    result = await service.disconnect(sender, receiver, channel=channel)
    return _disconnection_result_to_dict(result)


# ---------------------------------------------------------------------------
# Hot-plug per-creature wire
# ---------------------------------------------------------------------------


async def wire_creature(
    service: "TerrariumService",
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

    Routes via ``service.wire_creature`` so multi-node deployments
    reach the creature's home node instead of always touching the
    host's local engine.
    """
    await service.wire_creature(
        session_id, creature_id, channel, direction, enabled=enabled
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
