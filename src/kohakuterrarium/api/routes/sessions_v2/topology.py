"""Sessions topology — channels + connect/disconnect.

Mounted at ``/api/sessions/topology``. Replaces the legacy
``/api/terrariums/{id}/channels*`` and the per-creature wire endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.schemas import ChannelAdd, ChannelSend, WireChannel
from kohakuterrarium.studio.sessions import topology as topology_lib
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ConnectPayload(BaseModel):
    """Body for ``POST /api/sessions/topology/{sid}/connect``."""

    sender: str
    receiver: str
    channel: str | None = None
    channel_type: str = "queue"


class DisconnectPayload(BaseModel):
    sender: str
    receiver: str
    channel: str | None = None


@router.post("/{a_session_id}/merge/{b_session_id}")
async def merge_sessions(
    a_session_id: str,
    b_session_id: str,
    channel: str | None = None,
    service: TerrariumService = Depends(get_service),
):
    """Bridge two sessions so a creature in one can reach a channel in
    the other — the graph editor's cross-molecule wire.

    ``channel`` (query param, optional): when set, the underlying
    ``service.connect`` reuses that channel name instead of creating
    a fresh auto-named bridge.  This is what the frontend passes when
    the user dragged from an EXISTING channel — without it the connect
    path would create a parallel ``{a}_to_{b}`` channel alongside the
    user's, which is the wrong UX.

    Service-routed: both session ids resolve through the
    ``TerrariumService`` Protocol, so host-local AND worker-hosted
    graphs are found.  The bridge goes through ``service.connect``:

    - both creatures on the **same node** → an engine-level graph
      merge on that node (the original behaviour);
    - creatures on **different worker nodes** → a cross-node connect
      (the broadcast bridge) — merging into one engine graph is
      physically impossible across separate worker processes.
    """
    if not a_session_id or not b_session_id:
        raise HTTPException(400, "both session ids are required")
    if a_session_id == b_session_id:
        return {"session_id": a_session_id, "merged": False}
    a_graph = await service.get_graph(a_session_id)
    if a_graph is None:
        raise HTTPException(404, f"session {a_session_id!r} not found")
    b_graph = await service.get_graph(b_session_id)
    if b_graph is None:
        raise HTTPException(404, f"session {b_session_id!r} not found")
    if not a_graph.creature_ids or not b_graph.creature_ids:
        raise HTTPException(400, "cannot merge a session with no creatures")
    a_cid = next(iter(a_graph.creature_ids))
    b_cid = next(iter(b_graph.creature_ids))
    try:
        result = await service.connect(a_cid, b_cid, channel=channel)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    keep_gid = getattr(result, "graph_id", None) or a_session_id
    return {"session_id": keep_gid, "merged": True}


@router.get("/{session_id}/channels")
async def list_session_channels(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    """List shared channels in a session.

    Service-routed: works for both host-local and worker-hosted graphs.
    An unknown session is a 404 — ``topology_lib.list_channels`` itself
    returns ``()`` for any unknown id (it fans out across workers and
    can't tell "no channels" from "no session"), so the route makes
    the existence check explicit via ``get_graph``.
    """
    if await service.get_graph(session_id) is None:
        raise HTTPException(404, f"session {session_id!r} not found")
    try:
        return await topology_lib.list_channels(service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/channels")
async def add_session_channel(
    session_id: str,
    req: ChannelAdd,
    service: TerrariumService = Depends(get_service),
):
    """Declare a new shared channel in a session.

    Service-routed: works for both local and worker-hosted graphs.
    """
    try:
        info = await topology_lib.add_channel(
            service,
            session_id,
            req.name,
            channel_type=req.channel_type,
            description=req.description,
        )
        return {"status": "created", "channel": info}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/{session_id}/channels/{channel}")
async def get_session_channel(
    session_id: str,
    channel: str,
    service: TerrariumService = Depends(get_service),
):
    """Inspect a single shared channel.

    Service-routed: works for both host-local and worker-hosted graphs.
    An unknown session is a 404 (explicit existence check — see
    :func:`list_session_channels`); an unknown channel within a known
    session is also a 404.
    """
    if await service.get_graph(session_id) is None:
        raise HTTPException(404, f"session {session_id!r} not found")
    try:
        info = await topology_lib.channel_info(service, session_id, channel)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if info is None:
        raise HTTPException(404, f"Channel not found: {channel}")
    return info


@router.post("/{session_id}/channels/{channel}/send")
async def send_session_channel(
    session_id: str,
    channel: str,
    req: ChannelSend,
    service: TerrariumService = Depends(get_service),
):
    """Send a message to a shared channel.

    Service-routed: the helper ``send_to_channel`` walks
    ``host_engine_or_none(service)`` and surfaces a 404 in lab-host
    mode (channel objects live on workers, not on the host's empty
    coordination engine).  Full cross-node send needs a new Protocol
    method — out of scope for this migration.
    """
    try:
        msg_id = await topology_lib.send_to_channel(
            service, session_id, channel, req.content, req.sender
        )
        return {"message_id": msg_id, "status": "sent"}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/connect")
async def connect_creatures(
    session_id: str,
    req: ConnectPayload,
    service: TerrariumService = Depends(get_service),
):
    """Wire ``sender → receiver`` via a channel — may merge graphs.

    Service-routed so cross-node connect (sender + receiver on
    different cluster sites) triggers the
    :class:`MultiNodeTerrariumService` cross-site path: channel
    replicated on both nodes + terrarium.broadcast cross-subscribe.
    """
    # ``engine.connect`` (via ``terrarium/channels.py``) always emits a
    # ``TOPOLOGY_CHANGED`` event regardless of delta kind, so the route
    # MUST NOT emit a second one — subscribers would refresh prompts
    # twice and the session-store event log would carry duplicates.
    try:
        return await topology_lib.connect(
            service,
            req.sender,
            req.receiver,
            channel=req.channel,
            channel_type=req.channel_type,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/disconnect")
async def disconnect_creatures(
    session_id: str,
    req: DisconnectPayload,
    service: TerrariumService = Depends(get_service),
):
    """Drop the ``sender → receiver`` link — may split a graph.

    Service-routed so cross-node disconnect undoes the
    terrarium.broadcast cross-subscription on top of per-side wire
    removal.
    """
    try:
        return await topology_lib.disconnect(
            service, req.sender, req.receiver, channel=req.channel
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/creatures/{creature_id}/wire")
async def wire_session_creature(
    session_id: str,
    creature_id: str,
    req: WireChannel,
    service: TerrariumService = Depends(get_service),
):
    """Add a listen / send edge for a creature on an existing channel.

    Service-routed so multi-node deployments reach the creature's
    home node (``topology_lib.wire_creature`` was already
    service-routed; this is just the final route-layer migration).
    """
    # ``topology_lib.wire_creature`` emits TOPOLOGY_CHANGED itself;
    # do not double-emit here (see ``connect_creatures``).
    try:
        await topology_lib.wire_creature(
            service, session_id, creature_id, req.channel, req.direction, enabled=True
        )
        return {"status": "wired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.delete("/{session_id}/creatures/{creature_id}/wire")
async def unwire_session_creature(
    session_id: str,
    creature_id: str,
    req: WireChannel,
    service: TerrariumService = Depends(get_service),
):
    """Remove a listen / send edge for a creature on an existing channel."""
    try:
        await topology_lib.wire_creature(
            service,
            session_id,
            creature_id,
            req.channel,
            req.direction,
            enabled=False,
        )
        return {"status": "unwired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


# Note: a route-level ``_emit_topology_changed`` helper previously
# fired here after connect/wire/unwire.  Removed — the engine and the
# ``topology_lib`` helpers each emit their own TOPOLOGY_CHANGED at the
# point of mutation, and a second route-level emission gave subscribers
# (runtime-graph prompt block + session event log) duplicate events.
