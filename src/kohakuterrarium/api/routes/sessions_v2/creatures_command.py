"""Per-creature slash command, inventory, and explicit skill routes."""

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.dependencies import get_auth_config, get_optional_user
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.schemas import SlashCommand
from kohakuterrarium.terrarium.command_inventory import InvocationResolutionError
from kohakuterrarium.terrarium.service import TerrariumService

from ._helpers import resolve_creature_id

router = APIRouter()


def _command_authority(
    connection: HTTPConnection,
    user: User | None,
) -> tuple[str, bool]:
    """Derive command authority from authenticated request context."""
    config = get_auth_config(connection)
    if not config.multi_user_enabled:
        return "user:local", True
    if user is None:
        return "user:anonymous", False
    return f"user:{user.id}", user.role == "admin"


@router.get("/{session_id}/creatures/{creature_id}/command-inventory")
async def get_creature_command_inventory(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
) -> dict:
    """Return live commands and skills for one creature."""
    resolved = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.command_inventory(resolved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creature not found") from exc


@router.post("/{session_id}/creatures/{creature_id}/skill-input")
async def invoke_creature_skill(
    session_id: str,
    creature_id: str,
    body: SlashCommand,
    service: TerrariumService = Depends(get_service),
) -> dict:
    """Validate and inject a user-selected skill into the creature turn queue."""
    resolved = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.invoke_skill(
            resolved,
            body.command,
            body.args,
            source="web:skill",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creature not found") from exc
    except (InvocationResolutionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/creatures/{creature_id}/command")
async def execute_creature_command(
    session_id: str,
    creature_id: str,
    body: SlashCommand,
    connection: HTTPConnection,
    service: TerrariumService = Depends(get_service),
    user: User | None = Depends(get_optional_user),
) -> dict:
    """Execute a user command against a creature."""
    resolved = await resolve_creature_id(service, creature_id, session_id)
    principal, is_operator = _command_authority(connection, user)
    try:
        return await service.execute_command(
            resolved,
            body.command,
            body.args,
            principal=principal,
            is_operator=is_operator,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Creature not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
