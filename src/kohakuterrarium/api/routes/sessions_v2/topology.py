"""Sessions topology — channels + connect/disconnect.

Mounted at ``/api/sessions/topology``. Replaces the legacy
``/api/terrariums/{id}/channels*`` and the per-creature wire endpoint.
"""

import asyncio

import kohakuterrarium.terrarium.channels as _channels
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import ChannelAdd, ChannelSend, WireChannel
from kohakuterrarium.studio.sessions import topology as topology_lib
from kohakuterrarium.terrarium.events import EngineEvent, EventKind

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
    a_session_id: str, b_session_id: str, engine=Depends(get_engine)
):
    """Merge two sessions (graphs) into one without creating any
    bridge channel.

    Used by the graph editor when the user wires a creature to a
    channel that lives in a different molecule — both creatures need
    to share an engine graph to actually share the channel object.
    Returns the surviving session id (== graph id).
    """
    if not a_session_id or not b_session_id:
        raise HTTPException(400, "both session ids are required")
    if a_session_id == b_session_id:
        return {"session_id": a_session_id, "merged": False}
    graph_ids = {g.graph_id for g in engine.list_graphs()}
    if a_session_id not in graph_ids:
        raise HTTPException(404, f"session {a_session_id!r} not found")
    if b_session_id not in graph_ids:
        raise HTTPException(404, f"session {b_session_id!r} not found")
    a_graph = engine.get_graph(a_session_id)
    b_graph = engine.get_graph(b_session_id)
    if not a_graph.creature_ids or not b_graph.creature_ids:
        raise HTTPException(400, "cannot merge a session with no creatures")
    a_cid = next(iter(a_graph.creature_ids))
    b_cid = next(iter(b_graph.creature_ids))
    keep_gid = await _channels.ensure_same_graph(engine, a_cid, b_cid)
    return {"session_id": keep_gid, "merged": True}


@router.get("/{session_id}/channels")
async def list_session_channels(session_id: str, engine=Depends(get_engine)):
    """List shared channels in a session."""
    try:
        return await asyncio.to_thread(topology_lib.list_channels, engine, session_id)
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
        info = await asyncio.to_thread(
            topology_lib.channel_info, engine, session_id, channel
        )
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
        result = await topology_lib.connect(
            engine,
            req.sender,
            req.receiver,
            channel=req.channel,
            channel_type=req.channel_type,
        )
        delta_kind = result.get("delta", {}).get("kind")
        if delta_kind == "nothing":
            _emit_topology_changed(
                engine,
                result.get("graph_id") or session_id,
                req.sender,
                "connect",
                result.get("channel") or req.channel or "",
                "send",
            )
        return result
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
            engine, session_id, creature_id, req.channel, req.direction, enabled=True
        )
        _emit_topology_changed(
            engine, session_id, creature_id, "wire", req.channel, req.direction
        )
        return {"status": "wired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.delete("/{session_id}/creatures/{creature_id}/wire")
async def unwire_session_creature(
    session_id: str,
    creature_id: str,
    req: WireChannel,
    engine=Depends(get_engine),
):
    """Remove a listen / send edge for a creature on an existing channel."""
    try:
        await topology_lib.wire_creature(
            engine, session_id, creature_id, req.channel, req.direction, enabled=False
        )
        _emit_topology_changed(
            engine, session_id, creature_id, "unwire", req.channel, req.direction
        )
        return {"status": "unwired"}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


def _emit_topology_changed(
    engine,
    session_id: str,
    creature_id: str,
    status: str,
    channel: str,
    direction: str,
) -> None:
    emit = getattr(engine, "_emit", None)
    if not callable(emit):
        return
    emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            graph_id=session_id,
            creature_id=creature_id,
            channel=channel,
            payload={
                "kind": "channel_wiring",
                "direction": direction,
                "status": status,
            },
        )
    )
