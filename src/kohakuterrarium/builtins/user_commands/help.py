"""Help command — list available slash commands."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_list,
)


@register_user_command("help")
class HelpCommand(BaseUserCommand):
    name = "help"
    aliases = ["h", "?"]
    description = "Show available commands"
    layer = CommandLayer.INPUT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        registry = context.extra.get("command_registry", {})

        lines = ["Available commands:", ""]
        items = []
        for cmd in registry.values():
            alias_str = ""
            if cmd.aliases:
                alias_str = f" (aliases: {', '.join('/' + a for a in cmd.aliases)})"
            lines.append(f"  /{cmd.name:<12} {cmd.description}{alias_str}")
            items.append(
                {
                    "label": f"/{cmd.name}",
                    "description": cmd.description,
                    "aliases": [f"/{a}" for a in cmd.aliases],
                    "layer": cmd.layer.value,
                }
            )

        lines.extend(
            [
                "",
                "Keyboard shortcuts:",
                "",
                "  Enter              Submit message",
                "  Shift+Enter / Ctrl+Enter / Alt+Enter / Ctrl+J   Insert newline",
                "  Backslash at EOL   Soft newline continuation on Enter",
                "  Up / Down          Move in buffer (falls through to history at edges)",
                "  Alt+P / Alt+N      Force prev / next in history",
                "  Ctrl+R             Reverse-incremental history search",
                "  Ctrl+A / Ctrl+E    Start / end of line",
                "  Ctrl+W             Delete previous word",
                "  Ctrl+K / Ctrl+U    Kill to end / start of line",
                "  Ctrl+Y             Yank (paste kill-ring)",
                "",
                "  Esc                Interrupt the running agent",
                "  Ctrl+C             Clear input, or Ctrl+C again to interrupt",
                "  Ctrl+D             Quit",
                "  Ctrl+L             Clear the screen",
                "  Ctrl+O             Expand/collapse the most recent tool block",
                "  Ctrl+B             Send the running tool to background",
                "  Ctrl+X             Cancel the most recent backgrounded tool",
                "",
                "  /                  Open slash-command hint bar (try /model, /help, /exit)",
                "  @file              (completer) insert a file reference",
                "",
                "Model picker (opens on `/model` with no args):",
                "",
                "  Up / Down          Move through preset list",
                "  Left / Right       Cycle variation option on the hovered row",
                "  Tab / Shift+Tab    Switch which variation group is being cycled",
                "  Enter              Apply selected preset + variations",
                "  Esc                Cancel",
                "",
            ]
        )

        return UserCommandResult(
            output="\n".join(lines),
            data=ui_list("Commands", items),
        )
