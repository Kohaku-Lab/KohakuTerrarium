"""Clear command — clear conversation context (keeps session history)."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_confirm,
    ui_notify,
)


def _do_clear(context: UserCommandContext) -> str:
    """Execute the clear: wipe context, emit event, save snapshot."""
    agent = context.agent
    msgs = len(agent.controller.conversation.get_messages())
    agent.controller.conversation.clear()

    # Emit context_cleared event (for TUI/frontend display + session log)
    agent.output_router.notify_activity(
        "context_cleared",
        f"Cleared {msgs} messages",
        metadata={"messages_cleared": msgs},
    )

    # Save conversation snapshot AFTER clearing (so resume gets cleared state)
    if agent.session_store:
        agent.session_store.save_conversation(
            agent.config.name,
            agent.controller.conversation.to_messages(),
        )

    return f"Conversation cleared ({msgs} messages removed from context)."


@register_user_command("clear")
class ClearCommand(BaseUserCommand):
    name = "clear"
    aliases = []
    description = "Clear conversation context (history preserved in session)"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")

        # --force skips confirmation (used by frontend after confirm dialog)
        if args.strip() == "--force":
            msg = _do_clear(context)
            return UserCommandResult(
                output=msg,
                data=ui_notify("Context cleared", level="success"),
            )

        msgs = len(context.agent.controller.conversation.get_messages())

        # CLI/TUI: clear immediately (no confirmation)
        if context.input_module:
            msg = _do_clear(context)
            return UserCommandResult(output=msg)

        # Web frontend: return confirm dialog
        return UserCommandResult(
            output=f"Clear {msgs} messages?",
            data=ui_confirm(
                f"Clear {msgs} messages from conversation context?\n"
                "Chat history will be preserved in the session log.",
                action="clear",
                action_args="--force",
            ),
        )
