"""WebSocket PTY terminal endpoint (collapsed).

Single endpoint ``/ws/sessions/{sid}/creatures/{cid}/pty`` replaces
the legacy pair (``/ws/terminal/{agent_id}`` and
``/ws/terminal/terrariums/{terrarium_id}/{target}``). Resolution is
engine-backed: ``engine.get_creature(cid)`` looks up the working
directory, and ``sid`` is informational (the routing path is part of
the URL contract but the engine treats every creature uniformly).

Wire format (server ↔ client):

    Client → Server: { "type": "input",  "data": "ls\\n" }
    Client → Server: { "type": "resize", "rows": 24, "cols": 80 }
    Server → Client: { "type": "output", "data": "..." }
    Server → Client: { "type": "error",  "data": "..." }
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.attach.pty_router import _session_cwd, pty_session
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{sid}/creatures/{cid}/pty")
async def session_pty_ws(websocket: WebSocket, sid: str, cid: str):
    """Interactive terminal in the working directory of a creature."""
    await websocket.accept()

    engine = get_engine()
    try:
        creature = find_creature(engine, sid, cid)
    except KeyError:
        await websocket.send_json(
            {"type": "error", "data": f"creature {cid!r} not found"}
        )
        await websocket.close()
        return

    cwd = _session_cwd(creature)
    logger.info("Pty session", sid=sid, cid=cid, cwd=cwd)

    try:
        await pty_session(websocket, cwd)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("pty WS error", error=str(e), exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
