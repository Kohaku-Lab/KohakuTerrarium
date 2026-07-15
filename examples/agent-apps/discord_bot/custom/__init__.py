"""Expose the Discord bot's shared I/O, triggers, and emoji tools.

This package provides:
- DiscordInputModule: Receives messages from Discord
- DiscordOutputModule: Sends messages to Discord
- DiscordPingTrigger: Fires when bot is mentioned
- DiscordIdleTrigger: Fires after chat inactivity
- EmojiSearchTool: Search guild emojis by keyword/description
- EmojiListTool: List available emojis
- EmojiGetTool: Get emoji by name or ID

The input module registers a Discord client to a shared registry,
which allows the output module and triggers to access the same
connection without needing to be explicitly linked.

Individual modules use bare imports for sibling files (e.g. ``from discord_client import ...``)
because the framework's ModuleLoader temporarily adds this directory to sys.path when
loading custom modules.  For the package-import pathway (``from custom import ...``),
this __init__ adds the same directory so the bare imports resolve identically.
"""

import sys
from pathlib import Path

# Mirror ModuleLoader's path setup so sibling bare imports also work through
# the package-import pathway.
_CUSTOM_DIR = str(Path(__file__).parent)
if _CUSTOM_DIR not in sys.path:
    sys.path.insert(0, _CUSTOM_DIR)

from discord_io import (
    DiscordClient,
    DiscordInputModule,
    DiscordMessage,
    DiscordOutputModule,
    create_discord_io,
)
from discord_trigger import (
    DiscordActivityMonitor,
    DiscordIdleTrigger,
    DiscordPingTrigger,
)
from emoji_db import EmojiDatabase, EmojiRecord, get_emoji_db, save_emoji_db
from emoji_search import (
    EmojiGetTool,
    EmojiListTool,
    EmojiSearchTool,
    create_emoji_tools,
)

__all__ = [
    "DiscordClient",
    "DiscordInputModule",
    "DiscordOutputModule",
    "DiscordMessage",
    "DiscordPingTrigger",
    "DiscordIdleTrigger",
    "DiscordActivityMonitor",
    "create_discord_io",
    "EmojiSearchTool",
    "EmojiListTool",
    "EmojiGetTool",
    "create_emoji_tools",
    "EmojiDatabase",
    "EmojiRecord",
    "get_emoji_db",
    "save_emoji_db",
]
