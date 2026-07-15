"""Expose guarded Discord output through native tool calling.

Wraps the Discord output functionality (keyword filtering, dedup,
drop chance, reply/mention markers) into a callable tool for
native function calling mode.
"""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

from discord_output import DiscordOutputModule

logger = get_logger("kohakuterrarium.custom.send_discord")


class SendDiscordTool(BaseTool):
    """Send messages through the shared output policy and Discord client.

    Supports special markers in message content:
    - [reply:Username] or [reply:#N] - reply to a message
    - [@Username] - mention a user
    """

    def __init__(
        self,
        client_name: str = "default",
        filtered_keywords: list[str] | None = None,
        keywords_file: str | None = None,
        drop_base_chance: float = 0.25,
        drop_increment: float = 0.15,
        drop_max_chance: float = 0.7,
        dedup_threshold: float = 0.85,
        dedup_window: int = 5,
        config: ToolConfig | None = None,
    ):
        super().__init__(config)
        self._output = DiscordOutputModule(
            client_name=client_name,
            filtered_keywords=filtered_keywords,
            keywords_file=keywords_file,
            drop_base_chance=drop_base_chance,
            drop_increment=drop_increment,
            drop_max_chance=drop_max_chance,
            dedup_threshold=dedup_threshold,
            dedup_window=dedup_window,
        )

    @property
    def tool_name(self) -> str:
        return "send_discord"

    @property
    def description(self) -> str:
        return "Send a message to the current Discord channel"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        """Describe the message argument for native function calling."""
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message content to send to Discord",
                },
            },
            "required": ["message"],
        }

    async def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Validate and send one message through Discord output policy."""
        message = args.get("message", args.get("content", "")).strip()
        if not message:
            return ToolResult(error="Empty message - nothing to send")

        try:
            await self._output.write(message)
            return ToolResult(output="Sent", exit_code=0)
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to send Discord message", error=error_msg)
            return ToolResult(error=f"Failed to send: {error_msg}")

    def get_full_documentation(self) -> str:
        return """# send_discord

Send a message to the current Discord channel.

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| message | string | Message content to send |

## Special Markers

Include these in your message content for special behavior:

- `[reply:Username]` - Reply to the user's latest message
- `[reply:#N]` - Reply to message number N
- `[@Username]` - Mention/ping a user

## Behavior

- Messages may be silently dropped if new messages are pending (avoids outdated responses)
- Near-duplicate messages within a short window are filtered
- Filtered keywords are replaced with [filtered]
- Very short or garbage-pattern messages are filtered

## Tips

- Split long responses into multiple send_discord calls (1-2 lines each)
- Call multiple times for natural group chat cadence
- Use reply markers to respond to specific messages
"""
