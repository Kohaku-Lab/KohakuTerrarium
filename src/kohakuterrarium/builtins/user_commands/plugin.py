"""Plugin command — list and toggle plugins at runtime."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_select,
)


@register_user_command("plugin")
class PluginCommand(BaseUserCommand):
    name = "plugin"
    aliases = ["plugins"]
    description = "List plugins or toggle enable/disable"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")

        mgr = context.agent.plugins
        if not mgr:
            return UserCommandResult(output="No plugins loaded.")

        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0] if parts else ""

        # /plugin toggle <name>
        if subcmd == "toggle" and len(parts) > 1:
            name = parts[1].strip()
            if mgr.is_enabled(name):
                mgr.disable(name)
                return UserCommandResult(output=f"Plugin '{name}' disabled.")
            elif mgr.enable(name):
                await mgr.load_pending()
                return UserCommandResult(output=f"Plugin '{name}' enabled.")
            else:
                return UserCommandResult(error=f"Plugin not found: {name}")

        # /plugin enable <name>
        if subcmd == "enable" and len(parts) > 1:
            name = parts[1].strip()
            if mgr.enable(name):
                await mgr.load_pending()
                return UserCommandResult(output=f"Plugin '{name}' enabled.")
            return UserCommandResult(error=f"Plugin not found: {name}")

        # /plugin disable <name>
        if subcmd == "disable" and len(parts) > 1:
            name = parts[1].strip()
            if mgr.disable(name):
                return UserCommandResult(output=f"Plugin '{name}' disabled.")
            return UserCommandResult(error=f"Plugin not found: {name}")

        # /plugin (list)
        plugins = mgr.list_plugins()
        if not plugins:
            return UserCommandResult(output="No plugins loaded.")

        lines = []
        options = []
        for p in plugins:
            status = "enabled" if p["enabled"] else "disabled"
            description = p.get("description") or "No description"
            lines.append(
                f"{status:>8}  {p['name']:<24} p{p['priority']}  {description}"
            )
            options.append(
                {
                    "value": f"toggle {p['name']}",
                    "label": p["name"],
                    "description": description,
                    "status": status,
                    "priority": p["priority"],
                    "selected": p["enabled"],
                }
            )

        lines.append("")
        lines.append(
            "Select a plugin to toggle it, or use /plugin enable|disable <name>"
        )
        lines.append(
            "Tip: /module covers plugins + provider-native tools in one surface."
        )

        return UserCommandResult(
            output="\n".join(lines),
            data=ui_select(
                "Plugins",
                options,
                action="plugin",
            ),
        )
