"""Preserve the original Discord I/O import surface after module splitting."""

from discord_client import (
    DiscordClient,
    DiscordMessage,
    RecentMessage,
    get_client,
    register_client,
    short_id,
)
from discord_input import DiscordInputModule
from discord_output import DiscordOutputModule, create_discord_io

__all__ = [
    "DiscordClient",
    "DiscordMessage",
    "RecentMessage",
    "DiscordInputModule",
    "DiscordOutputModule",
    "create_discord_io",
    "get_client",
    "register_client",
    "short_id",
]
