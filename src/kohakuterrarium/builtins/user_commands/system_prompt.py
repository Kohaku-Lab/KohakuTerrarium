"""``/system_prompt`` slash command — show the current system prompt.

Read-only view of what the agent is currently telling the model in
its system message (after prompt-aggregation, plugin contributions,
and runtime hints). Useful for debugging "why is the agent doing
X?" without poking the LLM.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("system_prompt")
class SystemPromptCommand(BaseUserCommand):
    name = "system_prompt"
    aliases = ["sysprompt", "system-prompt"]
    description = "Show the agent's current effective system prompt (read-only)."
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")
        get_prompt = getattr(agent, "get_system_prompt", None)
        if not callable(get_prompt):
            return UserCommandResult(
                error="This agent does not expose get_system_prompt."
            )
        try:
            text = get_prompt() or ""
        except Exception as exc:
            return UserCommandResult(error=f"Failed to read system prompt: {exc}")
        if not text.strip():
            return UserCommandResult(output="(system prompt is empty)")
        return UserCommandResult(output=text)
