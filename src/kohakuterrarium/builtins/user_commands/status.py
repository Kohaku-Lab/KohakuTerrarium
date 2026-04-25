"""Status command — show agent/session info."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_info_panel,
)


@register_user_command("status")
class StatusCommand(BaseUserCommand):
    name = "status"
    aliases = ["info"]
    description = "Show agent/session status"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")

        agent = context.agent
        model = getattr(agent.llm, "model", "unknown")
        msgs = len(agent.controller.conversation.get_messages())
        tools = len(agent.registry.list_tools())
        running_jobs = len(agent.executor.get_running_jobs())
        compacting = (
            agent.compact_manager.is_compacting if agent.compact_manager else False
        )

        fields = [
            {"key": "Agent", "value": agent.config.name},
            {"key": "Model", "value": model},
            {"key": "Messages", "value": str(msgs)},
            {"key": "Tools", "value": str(tools)},
            {"key": "Running jobs", "value": str(running_jobs)},
            {"key": "Compacting", "value": "yes" if compacting else "no"},
        ]

        # Plain text
        lines = [f"{f['key']:<14} {f['value']}" for f in fields]

        return UserCommandResult(
            output="\n".join(lines),
            data=ui_info_panel("Agent Status", fields),
        )
