"""``/workspace`` slash command — view or change the agent's working dir.

Mirrors the Vue ``WorkspaceTab.vue`` form: ``/workspace`` shows the
current directory, ``/workspace <path>`` switches it. Goes through
:class:`WorkspaceController` which already validates the path,
re-roots :class:`PathBoundaryGuard`, and clears
:class:`FileReadState` — same code path as the HTTP API.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("workspace")
class WorkspaceCommand(BaseUserCommand):
    name = "workspace"
    aliases = ["pwd", "cwd"]
    description = "Show or switch the agent's working directory: /workspace [<path>]"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")
        ws = getattr(agent, "workspace", None)
        if ws is None:
            return UserCommandResult(error="This agent has no workspace controller.")
        path = (args or "").strip()
        if not path:
            return UserCommandResult(output=f"Working directory: {ws.get()}")
        try:
            applied = ws.set(path)
        except (OSError, RuntimeError, ValueError) as exc:
            return UserCommandResult(error=str(exc))
        return UserCommandResult(output=f"Working directory → {applied}")
