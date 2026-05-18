"""``/channels`` — list the channels the focused creature participates in."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("channels")
class ChannelsCommand(BaseUserCommand):
    name = "channels"
    aliases = []
    description = "List channels the focused creature participates in"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        engine = (context.extra or {}).get("engine")
        cid = (context.extra or {}).get("creature_id", "")
        if engine is None or not cid:
            return UserCommandResult(error="no creature context")
        try:
            creature = engine.get_creature(cid)
        except Exception:
            return UserCommandResult(error=f"unknown creature: {cid}")
        listen = list(getattr(creature, "listen_channels", []) or [])
        send = list(getattr(creature, "send_channels", []) or [])
        if not listen and not send:
            return UserCommandResult(
                output=f"{creature.name}: no channels (single-creature setup?)"
            )
        lines = [f"Channels for {creature.name}:"]
        if listen:
            lines.append(f"  listen: {', '.join(listen)}")
        if send:
            lines.append(f"  send:   {', '.join(send)}")
        return UserCommandResult(output="\n".join(lines))
