"""Per-creature slash command execution route."""

from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.dependencies import get_auth_config, get_optional_user
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.api.schemas import SlashCommand
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _command_principal(
    conn_info: HTTPConnection, user: User | None
) -> tuple[str, bool]:
    """Derive the slash-command principal and operator flag.

    The single-tenant console is trusted, anonymous multi-user callers remain
    unprivileged, and only authenticated administrators receive operator authority.
    """
    cfg = get_auth_config(conn_info)
    if user is None:
        if cfg.multi_user_enabled:
            return "user:anonymous", False
        return "user:local", True
    is_admin = user.role == "admin"
    return f"user:{user.id}", is_admin


@router.post("/{session_id}/creatures/{creature_id}/command")
async def execute_creature_command(
    session_id: str,
    creature_id: str,
    req: SlashCommand,
    conn_info: HTTPConnection,
    service: TerrariumService = Depends(get_service),
    user: User | None = Depends(get_optional_user),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    principal, is_operator = _command_principal(conn_info, user)
    try:
        return await service.execute_command(
            cid, req.command, req.args, principal=principal, is_operator=is_operator
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
