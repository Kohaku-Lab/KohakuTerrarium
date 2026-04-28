"""Per-creature slash command execution route."""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import SlashCommand
from kohakuterrarium.studio.sessions import creature_command

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/command")
async def execute_creature_command(
    session_id: str,
    creature_id: str,
    req: SlashCommand,
    engine=Depends(get_engine),
):
    try:
        return await creature_command.execute_command(
            engine, session_id, creature_id, req.command, req.args
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
