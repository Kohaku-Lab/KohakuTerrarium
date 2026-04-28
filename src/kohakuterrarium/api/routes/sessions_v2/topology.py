"""Sessions topology — channels + connect/disconnect.

Mounted at ``/api/sessions/topology``. Replaces the legacy
``/api/terrariums/{id}/channels*`` and the per-creature wire endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import ChannelAdd, ChannelSend, WireChannel
from kohakuterrarium.studio.sessions import topology as topology_lib

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


@router.get("/{session_id}/channels")
async def list_session_channels(session_id: str, engine=Depends(get_engine)):
    """List shared channels in a session."""
    try:
        return topology_lib.list_channels(engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/channels")
async def add_session_channel(
    session_id: str, req: ChannelAdd, engine=Depends(get_engine)
):
    """Declare a new shared channel in a session."""
    try:
        info = await topology_lib.add_channel(
            engine,
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
    session_id: str, channel: str, engine=Depends(get_engine)
):
    """Inspect a single shared channel."""
    try:
        info = topology_lib.channel_info(engine, session_id, channel)
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
    engine=Depends(get_engine),
):
    """Send a message to a shared channel."""
    try:
        msg_id = await topology_lib.send_to_channel(
            engine, session_id, channel, req.content, req.sender
        )
        return {"message_id": msg_id, "status": "sent"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/connect")
async def connect_creatures(
    session_id: str, req: ConnectPayload, engine=Depends(get_engine)
):
    """Wire ``sender → receiver`` via a channel — may merge graphs."""
    try:
        return await topology_lib.connect(
            engine,
            req.sender,
            req.receiver,
            channel=req.channel,
            channel_type=req.channel_type,
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/disconnect")
async def disconnect_creatures(
    session_id: str, req: DisconnectPayload, engine=Depends(get_engine)
):
    """Drop the ``sender → receiver`` link — may split a graph."""
    try:
        return await topology_lib.disconnect(
            engine, req.sender, req.receiver, channel=req.channel
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/creatures/{creature_id}/wire")
async def wire_session_creature(
    session_id: str,
    creature_id: str,
    req: WireChannel,
    engine=Depends(get_engine),
):
    """Add a listen / send edge for a creature on an existing channel."""
    try:
        await topology_lib.wire_creature(
            engine, session_id, creature_id, req.channel, req.direction
        )
        return {"status": "wired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
