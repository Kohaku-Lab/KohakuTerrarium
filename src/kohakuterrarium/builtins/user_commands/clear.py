"""Clear live conversation context while preserving recorded session history."""

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
    """Clear context, emit its activity, persist the empty snapshot, and summarize."""
    agent = context.agent
    msgs = len(agent.controller.conversation.get_messages())
    agent.controller.conversation.clear()

    # Activity notification keeps frontends and the session event log synchronized.
    agent.output_router.notify_activity(
        "context_cleared",
        f"Cleared {msgs} messages",
        metadata={"messages_cleared": msgs},
    )

    # Persisting after mutation ensures resume restores the cleared state.
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

        # Confirming frontends resubmit with ``--force`` to avoid a second prompt.
        if args.strip() == "--force":
            msg = _do_clear(context)
            return UserCommandResult(
                output=msg,
                data=ui_notify("Context cleared", level="success"),
            )

        msgs = len(context.agent.controller.conversation.get_messages())

        # Interactive terminal input provides an immediate command path.
        if context.input_module:
            msg = _do_clear(context)
            return UserCommandResult(output=msg)

        # Frontends without an input module must confirm before destructive clearing.
        return UserCommandResult(
            output=f"Clear {msgs} messages?",
            data=ui_confirm(
                f"Clear {msgs} messages from conversation context?\n"
                "Chat history will be preserved in the session log.",
                action="clear",
                action_args="--force",
            ),
        )
