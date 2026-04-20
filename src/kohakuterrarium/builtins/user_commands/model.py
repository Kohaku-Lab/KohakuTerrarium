"""Model command — list or switch LLM models."""

from kohakuterrarium.builtins.user_commands import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_notify,
    ui_select,
)


@register_user_command("model")
class ModelCommand(BaseUserCommand):
    name = "model"
    aliases = ["llm"]
    description = "List models or switch: /model [name]"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not args:
            return self._list_models(context)
        return self._switch_model(args.strip(), context)

    def _list_models(self, context: UserCommandContext) -> UserCommandResult:
        from kohakuterrarium.llm.profiles import list_all

        entries = list_all()
        current = ""
        current_name = ""
        if context.agent:
            current = getattr(context.agent.llm, "model", "")
            current_name = getattr(context.agent, "_llm_override", "") or getattr(
                context.agent.config, "llm_profile", ""
            )

        available = [e for e in entries if e.get("available")]

        # Plain text for CLI/TUI
        lines = [f"Current model: {current}", ""]
        if available:
            lines.append("Available models:")
            for e in available:
                marker = (
                    " *"
                    if e["name"] == current_name
                    or (not current_name and e["model"] == current)
                    else ""
                )
                variations = e.get("variation_groups") or {}
                variation_note = ""
                if variations:
                    parts = []
                    for group_name in sorted(variations):
                        options = sorted((variations[group_name] or {}).keys())
                        parts.append(f"{group_name}={{{'|'.join(options)}}}")
                    variation_note = "  [" + "; ".join(parts) + "]"
                lines.append(
                    f"  {e['name']:<25} {e['model']:<35} "
                    f"({e['login_provider']}){variation_note}{marker}"
                )
        else:
            lines.append("No models with API keys configured.")
            lines.append("Run: kt login <provider>")
        lines.append("")
        lines.append("Switch: /model <name>  (or /model name@group=option,…)")

        return UserCommandResult(
            output="\n".join(lines),
            data=ui_select(
                "Switch Model",
                [
                    {
                        "value": e["name"],
                        "label": e["name"],
                        "model": e["model"],
                        "provider": e.get("login_provider", ""),
                        "context": f"{e.get('max_context', 0) // 1000}k",
                        "variation_groups": e.get("variation_groups", {}),
                        "selected": e["name"] == current_name
                        or (not current_name and e["model"] == current),
                    }
                    for e in available
                ],
                current=current,
                action="model",
            ),
        )

    def _switch_model(
        self, name: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context for model switching.")
        try:
            model = context.agent.switch_model(name)
            return UserCommandResult(
                output=f"Switched to: {model}",
                data=ui_notify(f"Model switched to {model}", level="success"),
            )
        except ValueError as e:
            return UserCommandResult(error=str(e))
