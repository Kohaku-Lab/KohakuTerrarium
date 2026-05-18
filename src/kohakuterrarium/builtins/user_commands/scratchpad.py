"""``/scratchpad`` — dump the focused creature's scratchpad state."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)

_LINE_CAP = 50


@register_user_command("scratchpad")
class ScratchpadCommand(BaseUserCommand):
    name = "scratchpad"
    aliases = ["pad"]
    description = "Show the focused creature's scratchpad"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="no focused creature")
        scratch = getattr(agent, "scratchpad", None)
        if scratch is None:
            return UserCommandResult(output="No scratchpad on this creature")
        # The scratchpad API is duck-typed across implementations;
        # try the common reads and fall back to repr().
        text = ""
        for getter in ("get_all", "all", "render", "to_text"):
            fn = getattr(scratch, getter, None)
            if callable(fn):
                try:
                    out = fn()
                except Exception as e:  # pragma: no cover - defensive
                    return UserCommandResult(error=f"scratchpad read failed: {e}")
                if isinstance(out, str):
                    text = out
                elif isinstance(out, dict):
                    text = "\n".join(f"{k}: {v}" for k, v in out.items())
                else:
                    text = str(out)
                break
        if not text:
            text = repr(scratch)
        lines = text.splitlines() or [text]
        if len(lines) > _LINE_CAP:
            lines = lines[:_LINE_CAP] + [f"… ({len(lines) - _LINE_CAP} more lines)"]
        return UserCommandResult(output="\n".join(lines))
