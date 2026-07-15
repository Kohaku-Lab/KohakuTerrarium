"""Expose searchable Discord guild emoji metadata as agent tools.

This tool allows the agent to find appropriate emojis from the database
built by emoji_builder.py.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

from emoji_db import DEFAULT_DB_PATH, EmojiDatabase, EmojiRecord, get_emoji_db

logger = get_logger("kohakuterrarium.custom.emoji_search")


class EmojiSearchTool(BaseTool):
    """Search emoji names, captions, and tags by relevance."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        config: ToolConfig | None = None,
    ):
        super().__init__(config)
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db: EmojiDatabase | None = None

    @property
    def tool_name(self) -> str:
        return "emoji_search"

    @property
    def description(self) -> str:
        return "Search guild emojis by keyword, emotion, or description"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def _get_db(self) -> EmojiDatabase:
        """Lazily load and cache the configured emoji database."""
        if self._db is None:
            self._db = get_emoji_db(self.db_path)
        return self._db

    def reload_db(self) -> None:
        """Replace the cached database with the latest file contents."""
        self._db = get_emoji_db(self.db_path)
        logger.info("Reloaded emoji database", extra=self._db.stats())

    async def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Search the database and format matching Discord markup.

        Args:
            query: Search query (required)
            limit: Max results (default: 5)
            animated: Filter - "only", "exclude", or "any" (default)
            guild_id: Filter by guild ID (optional)

        Returns:
            ToolResult with matching emojis
        """
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(error="Missing required argument: query")

        limit = int(args.get("limit", 5))
        animated_filter = args.get("animated", "any")
        guild_id = args.get("guild_id")

        if guild_id is not None:
            guild_id = int(guild_id)

        animated_only = animated_filter == "only"
        static_only = animated_filter == "exclude"

        db = self._get_db()
        results = db.search(
            query=query,
            limit=limit,
            guild_id=guild_id,
            animated_only=animated_only,
            static_only=static_only,
        )

        if not results:
            return ToolResult(
                output=f"No emojis found matching '{query}'",
                metadata={"query": query, "count": 0},
            )

        output_lines = [f"Found {len(results)} emoji(s) matching '{query}':", ""]

        for i, emoji in enumerate(results, 1):
            discord_fmt = emoji.to_discord_format()
            anim_tag = " (animated)" if emoji.animated else ""
            caption_preview = (
                emoji.caption[:80] + "..." if len(emoji.caption) > 80 else emoji.caption
            )

            output_lines.append(f"{i}. {discord_fmt} :{emoji.name}:{anim_tag}")
            if caption_preview:
                output_lines.append(f"   Caption: {caption_preview}")
            output_lines.append(f"   Guild: {emoji.guild_name}")
            output_lines.append("")

        return ToolResult(
            output="\n".join(output_lines),
            metadata={
                "query": query,
                "count": len(results),
                "emoji_ids": [e.emoji_id for e in results],
            },
        )

    def get_full_documentation(self) -> str:
        """Return detailed usage documentation for on-demand tool help."""
        return """# emoji_search

Search guild emojis by keyword, emotion, or description.

## Arguments

- `query` (required): Search query - matches emoji name, caption, and tags
- `limit` (optional): Maximum results to return (default: 5)
- `animated` (optional): Filter by animation - "only", "exclude", or "any" (default)
- `guild_id` (optional): Filter by specific guild ID

## Examples

Search for happy emojis:
```
[/emoji_search]
@@query=happy
[emoji_search/]
```

Search for animated cat emojis:
```
[/emoji_search]
@@query=cat
@@animated=only
@@limit=3
[emoji_search/]
```

## Output

Returns a list of matching emojis with:
- Discord format string (e.g., `<:emoji_name:123456>`)
- Emoji name
- Caption/description
- Guild name

Use the Discord format string to include the emoji in your response.
"""


class EmojiListTool(BaseTool):
    """List available emojis with guild filtering and pagination."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        config: ToolConfig | None = None,
    ):
        super().__init__(config)
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db: EmojiDatabase | None = None

    @property
    def tool_name(self) -> str:
        return "emoji_list"

    @property
    def description(self) -> str:
        return "List available emojis, optionally filtered by guild"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def _get_db(self) -> EmojiDatabase:
        """Lazily load and cache the configured emoji database."""
        if self._db is None:
            self._db = get_emoji_db(self.db_path)
        return self._db

    async def _execute(self, args: dict[str, Any]) -> ToolResult:
        """List one paginated slice of available emojis.

        Args:
            guild_id: Filter by guild ID (optional)
            limit: Max results (default: 20)
            offset: Skip first N results (default: 0)

        Returns:
            ToolResult with emoji list
        """
        guild_id = args.get("guild_id")
        if guild_id is not None:
            guild_id = int(guild_id)

        limit = int(args.get("limit", 20))
        offset = int(args.get("offset", 0))

        db = self._get_db()
        all_emojis = db.list_all(guild_id)

        paginated = all_emojis[offset : offset + limit]

        if not paginated:
            return ToolResult(
                output="No emojis found"
                + (f" for guild {guild_id}" if guild_id else ""),
                metadata={"count": 0},
            )

        total = len(all_emojis)
        showing = f"Showing {offset + 1}-{offset + len(paginated)} of {total} emojis"

        output_lines = [showing, ""]

        for emoji in paginated:
            discord_fmt = emoji.to_discord_format()
            anim_tag = " (animated)" if emoji.animated else ""
            output_lines.append(
                f"- {discord_fmt} :{emoji.name}:{anim_tag} [{emoji.guild_name}]"
            )

        return ToolResult(
            output="\n".join(output_lines),
            metadata={
                "total": total,
                "showing": len(paginated),
                "offset": offset,
            },
        )

    def get_full_documentation(self) -> str:
        return """# emoji_list

List available emojis from the database.

## Arguments

- `guild_id` (optional): Filter by specific guild ID
- `limit` (optional): Maximum results to return (default: 20)
- `offset` (optional): Skip first N results for pagination (default: 0)

## Output

Returns a list of emojis with their Discord format strings and guild names.
"""


class EmojiGetTool(BaseTool):
    """Retrieve detailed metadata for an emoji by exact name or ID."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        config: ToolConfig | None = None,
    ):
        super().__init__(config)
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db: EmojiDatabase | None = None

    @property
    def tool_name(self) -> str:
        return "emoji_get"

    @property
    def description(self) -> str:
        return "Get emoji by exact name or ID"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def _get_db(self) -> EmojiDatabase:
        if self._db is None:
            self._db = get_emoji_db(self.db_path)
        return self._db

    async def _execute(self, args: dict[str, Any]) -> ToolResult:
        """
        Get emoji by name or ID.

        Args:
            name: Emoji name (exact match)
            id: Emoji ID

        Returns:
            ToolResult with emoji details
        """
        name = args.get("name", "").strip()
        emoji_id = args.get("id")

        if not name and not emoji_id:
            return ToolResult(error="Provide either 'name' or 'id'")

        db = self._get_db()

        emoji: EmojiRecord | None = None

        if emoji_id:
            emoji = db.get_emoji(int(emoji_id))
        elif name:
            matches = db.get_by_name(name)
            if matches:
                emoji = matches[0]  # Exact names may exist in multiple guilds.

        if not emoji:
            return ToolResult(
                output=f"Emoji not found: {name or emoji_id}",
                metadata={"found": False},
            )

        discord_fmt = emoji.to_discord_format()
        anim_tag = "Yes" if emoji.animated else "No"

        output_lines = [
            f"Emoji: {discord_fmt}",
            f"Name: {emoji.name}",
            f"ID: {emoji.emoji_id}",
            f"Animated: {anim_tag}",
            f"Guild: {emoji.guild_name}",
            "",
            f"Caption: {emoji.caption}" if emoji.caption else "Caption: (none)",
            "",
            f"Tags: {', '.join(emoji.tags)}" if emoji.tags else "Tags: (none)",
        ]

        return ToolResult(
            output="\n".join(output_lines),
            metadata={
                "found": True,
                "emoji_id": emoji.emoji_id,
                "discord_format": discord_fmt,
            },
        )

    def get_full_documentation(self) -> str:
        return """# emoji_get

Get detailed information about a specific emoji.

## Arguments

- `name` (optional): Exact emoji name to look up
- `id` (optional): Emoji ID to look up

At least one of `name` or `id` must be provided.

## Output

Returns detailed emoji information including:
- Discord format string
- Name, ID, animation status
- Guild name
- Caption and tags
"""


def create_emoji_tools(db_path: Path | str | None = None) -> list[BaseTool]:
    """Create search, listing, and exact-lookup tools for one database."""
    return [
        EmojiSearchTool(db_path),
        EmojiListTool(db_path),
        EmojiGetTool(db_path),
    ]
