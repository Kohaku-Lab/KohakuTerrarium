"""Catalog commands — list builtin user slash commands.

Replaces ``api.routes.configs.list_commands``.
"""

from fastapi import APIRouter

from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)

router = APIRouter()


@router.get("")
async def list_commands():
    """List available user slash commands."""
    result = []
    for name in list_builtin_user_commands():
        cmd = get_builtin_user_command(name)
        if cmd:
            result.append(
                {
                    "name": cmd.name,
                    "aliases": cmd.aliases,
                    "description": cmd.description,
                    "layer": cmd.layer.value,
                }
            )
    return result
