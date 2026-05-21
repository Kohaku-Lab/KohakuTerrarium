"""WebSocket PTY terminal endpoint.

Single endpoint ``/ws/sessions/{sid}/creatures/{cid}/pty``.

Multi-node aware: host-local creatures spawn the PTY in-process via
the existing ``studio.attach.pty_router.pty_session``.  Remote-worker
creatures open a ``terrarium.pty`` proxy stream through the unified
lab WS forwarder (``laboratory/ws_proxy.py``) — the worker spawns the
PTY in the creature's working directory and frames flow
bidirectionally over the lab transport.

Wire format (server ↔ client):

    Client → Server: { "type": "input",  "data": "ls\\n" }
    Client → Server: { "type": "resize", "rows": 24, "cols": 80 }
    Server → Client: { "type": "output", "data": "..." }
    Server → Client: { "type": "error",  "data": "..." }
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.laboratory.ws_proxy import proxy_ws_to_lab
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.attach.pty_router import _session_cwd, pty_session
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.service import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/sessions/{sid}/creatures/{cid}/pty")
async def session_pty_ws(
    websocket: WebSocket,
    sid: str,
    cid: str,
    service: TerrariumService = Depends(get_service),
):
    """Interactive terminal in the working directory of a creature."""
    await accept_with_auth_echo(websocket)

    # Lab-host has no host engine — ``host_engine_or_none`` returns
    # ``None`` and we go straight to the remote PTY-proxy branch.
    engine = host_engine_or_none(service)
    creature = None
    if engine is not None:
        try:
            creature = find_creature(engine, sid, cid)
        except Exception:  # noqa: BLE001 — local-lookup failure routes to remote
            creature = None
    if creature is None:
        try:
            info = await service.get_creature_info(cid)
        except Exception:
            info = None
        if info is not None:
            # Remote-hosted creature — open a PTY proxy stream to the
            # worker that hosts it.  The worker's TerrariumPtyAdapter
            # spawns the shell on its own machine in the creature's
            # working directory and bridges via the unified ws-proxy.
            home = await _resolve_creature_home(service, cid)
            if home is None or home == "_host":
                await websocket.send_json(
                    {"type": "error", "data": f"creature {cid!r} home unresolved"}
                )
                await websocket.close()
                return
            try:
                await proxy_ws_to_lab(
                    websocket=websocket,
                    sender=service.host,
                    demux=service.demux,
                    target_node=home,
                    namespace="terrarium.pty",
                    body={"creature_id": cid},
                )
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                logger.debug("remote PTY proxy error", error=str(exc), exc_info=True)
                try:
                    await websocket.close()
                except Exception:
                    pass
            return
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


async def _resolve_creature_home(service, cid: str) -> str | None:
    resolver = getattr(service, "_resolve_home", None)
    if resolver is None:
        return "_host"
    try:
        return await resolver(cid)
    except Exception:
        return None
