"""Per-creature model routes — switch."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import ModelSwitch
from kohakuterrarium.studio.sessions import creature_model

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/model")
async def switch_creature_model(
    session_id: str,
    creature_id: str,
    req: ModelSwitch,
    engine=Depends(get_engine),
):
    try:
        # ``switch_model`` reaches into LLM provider config + writes to
        # the session store's state slot — both sync. Keep off the loop.
        model = await asyncio.to_thread(
            creature_model.switch_model, engine, session_id, creature_id, req.model
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "switched", "model": model}
