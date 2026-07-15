"""Expose builtin user slash commands to catalog clients."""

from fastapi import APIRouter

from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)

router = APIRouter()


@router.get("")
async def list_commands():
    """List builtin slash commands with aliases and access layers."""
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
