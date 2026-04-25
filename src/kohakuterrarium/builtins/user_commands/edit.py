"""Edit command — edit a past user message and regenerate from it.

Usage in the in-session prompt::

    /edit <message_index> <new content>

``<message_index>`` is 0-based and refers to the index of the message
in the in-memory conversation. ``/status`` lists indices for visible
turns; in practice users typically retarget the last user message
(``/edit -1 new content`` works as a shortcut).

Each edit opens a new ``branch_id`` of the corresponding turn — the
original branch is preserved and addressable via the ``<1/N>``
navigator (frontend) or by manually inspecting the event log.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


def _parse_args(args: str) -> tuple[int | None, str]:
    """Split ``"<idx> <content>"`` into ``(idx, content)``.

    Returns ``(None, "")`` when args are malformed.
    """
    s = (args or "").strip()
    if not s:
        return None, ""
    parts = s.split(None, 1)
    try:
        idx = int(parts[0])
    except ValueError:
        return None, ""
    content = parts[1] if len(parts) > 1 else ""
    return idx, content


@register_user_command("edit")
class EditCommand(BaseUserCommand):
    name = "edit"
    aliases = []
    description = "Edit a past user message and re-run from it"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")
        idx, content = _parse_args(args)
        if idx is None or not content:
            return UserCommandResult(error="Usage: /edit <message_index> <new content>")
        # Resolve negative indices against the in-memory conversation so
        # ``-1`` targets the most recent user message.
        msgs = context.agent.controller.conversation.get_messages()
        n = len(msgs)
        if idx < 0:
            idx += n
        if idx < 0 or idx >= n:
            return UserCommandResult(
                error=f"Index {idx} out of range (conversation has {n} messages)"
            )
        await context.agent.edit_and_rerun(idx, content)
        return UserCommandResult(output=f"Edited message {idx} and re-running...")
