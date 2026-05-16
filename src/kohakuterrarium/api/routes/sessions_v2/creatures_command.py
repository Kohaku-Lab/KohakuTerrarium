"""Per-creature slash command execution route."""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import SlashCommand
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/command")
async def execute_creature_command(
    session_id: str,
    creature_id: str,
    req: SlashCommand,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id)
    try:
        return await service.execute_command(cid, req.command, req.args)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
