"""Inspect and explicitly replace missing historical local image references."""

from kohakuterrarium.core.conversation.media import repair_history_images
from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_confirm,
)


@register_user_command("repair-images")
class RepairImagesCommand(BaseUserCommand):
    name = "repair-images"
    description = "Inspect missing historical images and confirm their replacement"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if context.agent is None:
            return UserCommandResult(error="No agent context.")
        tokens = args.split()
        if tokens and (len(tokens) != 2 or tokens[0] != "--apply"):
            return UserCommandResult(
                error="Usage: /repair-images [--apply <confirmation>]"
            )
        confirmation = tokens[1] if tokens else None
        found, token = await repair_history_images(context.agent, confirmation)
        if confirmation is not None:
            return UserCommandResult(
                output=f"Replaced {len(found)} missing historical image(s) with text notes. Retry your message."
            )
        if not found:
            return UserCommandResult(
                output="No repairable missing historical images. Current, unfinished, and unidentified turns remain protected."
            )
        listing = "\n".join(
            f"- turn {item.turn_index}, message {item.message_index}, part {item.part_index}: {item.url}"
            for item in found
        )
        prompt = (
            f"Replace {len(found)} missing historical image(s) with text notes?\n{listing}\n"
            "Only this branch's context will change; recorded events are preserved."
        )
        return UserCommandResult(
            output=f"{prompt}\nConfirm with /repair-images --apply {token}",
            data=ui_confirm(prompt, action=self.name, action_args=f"--apply {token}"),
        )
