"""Expose session channel and graph-topology operations."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import (
    resolve_connect_target_id,
    resolve_creature_id,
)
from kohakuterrarium.api.schemas import ChannelAdd, ChannelSend, WireChannel
from kohakuterrarium.studio.sessions import topology as topology_lib
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ConnectPayload(BaseModel):
    """Describe a directed channel connection between two creatures."""

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
    """Bridge two sessions through representative creatures.

    An explicit ``channel`` reuses an existing channel instead of creating a
    parallel auto-named bridge. Same-node creatures merge into one engine graph;
    cross-node creatures use a broadcast bridge because process-local graphs
    cannot merge across workers.
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
    """List a session's shared channels.

    Check graph existence first because the service-wide lookup returns an empty
    result for both an unknown session and a session without channels.
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
    """Declare a shared channel in a local or worker-hosted session."""
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
    """Return one shared channel or distinguish missing sessions and channels."""
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
    """Send a message through a channel available on the host engine.

    Worker-owned channel objects are unavailable from an empty lab coordinator,
    so cross-node delivery requires a service protocol operation not exposed here.
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
    """Connect two creatures through a channel, merging graphs when possible.

    Cross-node connections replicate the channel and establish a broadcast
    subscription between sites.
    """
    # The URL session is authoritative: resolve both endpoints in that exact
    # session or logical cluster before allowing the topology mutation.
    try:
        sender_id = await resolve_creature_id(service, req.sender, session_id)
        receiver_id = await resolve_connect_target_id(service, req.receiver, session_id)
        return await topology_lib.connect(
            service,
            sender_id,
            receiver_id,
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
    """Disconnect two creatures and remove any cross-node subscription."""
    try:
        sender_id = await resolve_creature_id(service, req.sender, session_id)
        receiver_id = await resolve_creature_id(service, req.receiver, session_id)
        return await topology_lib.disconnect(
            service, sender_id, receiver_id, channel=req.channel
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
    """Add a creature's listen or send edge on an existing channel."""
    # The topology helper emits the change event; emitting here would duplicate it.
    try:
        cid = await resolve_creature_id(service, creature_id, session_id)
        await topology_lib.wire_creature(
            service, session_id, cid, req.channel, req.direction, enabled=True
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
        cid = await resolve_creature_id(service, creature_id, session_id)
        await topology_lib.wire_creature(
            service,
            session_id,
            cid,
            req.channel,
            req.direction,
            enabled=False,
        )
        return {"status": "unwired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
