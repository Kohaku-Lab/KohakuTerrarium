"""Per-creature model routes — switch."""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import ModelSwitch
from kohakuterrarium.studio.sessions import lifecycle as _session_lifecycle
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/model")
async def switch_creature_model(
    session_id: str,
    creature_id: str,
    req: ModelSwitch,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        model = await service.switch_model(cid, req.model)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Update the host-side ``_meta`` cache so the next sync read of
    # ``get_session`` (e.g. a chat-tab reopen that races a worker
    # disconnect) still surfaces the user's selection. Without this
    # the model chip flips to "No model" if the worker stops
    # answering between switch and the next read (B4).
    _session_lifecycle.update_remote_creature_model_meta(
        cid, model=model or "", llm_name=model or ""
    )
    return {"status": "switched", "model": model}
