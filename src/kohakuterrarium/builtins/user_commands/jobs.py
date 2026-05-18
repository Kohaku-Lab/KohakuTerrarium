"""``/jobs`` — list the focused creature's currently-running jobs."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("jobs")
class JobsCommand(BaseUserCommand):
    name = "jobs"
    aliases = []
    description = "List running jobs for the focused creature"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="no focused creature")
        executor = getattr(agent, "executor", None)
        if executor is None or not hasattr(executor, "get_running_jobs"):
            return UserCommandResult(output="No jobs (executor unavailable)")
        try:
            jobs = list(executor.get_running_jobs())
        except Exception as e:  # pragma: no cover - defensive
            return UserCommandResult(error=f"could not read jobs: {e}")
        if not jobs:
            return UserCommandResult(output="No running jobs")
        lines = []
        for job in jobs:
            jid = getattr(job, "job_id", "") or getattr(job, "id", "")
            name = getattr(job, "name", "") or getattr(job, "tool_name", "?")
            kind = getattr(job, "kind", "tool")
            lines.append(f"  {kind:<8} {name:<24} {jid}")
        return UserCommandResult(output="Running jobs:\n" + "\n".join(lines))
