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
        current_identifier = ""
        if context.agent:
            # Prefer the canonical ``provider/name[@variations]`` identifier
            # so the "Current model:" line matches the switcher output.
            get_ident = getattr(context.agent, "llm_identifier", None)
            current_identifier = get_ident() if callable(get_ident) else ""
            current = current_identifier or getattr(context.agent.llm, "model", "")
        current_name = current_identifier

        available = [e for e in entries if e.get("available")]

        def _identifier(entry: dict) -> str:
            """Canonical ``provider/name`` identifier for an entry."""
            provider = entry.get("provider") or entry.get("login_provider") or ""
            return f"{provider}/{entry['name']}" if provider else entry["name"]

        # Strip any ``@variations`` suffix from the current identifier
        # so the row match works regardless of selected options.
        current_base = (current_name.split("@", 1)[0]) if current_name else ""

        def _is_current(entry: dict) -> bool:
            ident = _identifier(entry)
            if current_base and current_base in {ident, entry["name"]}:
                return True
            return not current_base and entry["model"] == current

        # Plain text for CLI/TUI
        lines = [f"Current model: {current}", ""]
        if available:
            lines.append("Available models:")
            for e in available:
                marker = " *" if _is_current(e) else ""
                variations = e.get("variation_groups") or {}
                variation_note = ""
                if variations:
                    parts = []
                    for group_name in sorted(variations):
                        options = sorted((variations[group_name] or {}).keys())
                        parts.append(f"{group_name}={{{'|'.join(options)}}}")
                    variation_note = "  [" + "; ".join(parts) + "]"
                lines.append(
                    f"  {_identifier(e):<36} {e['model']:<35}{variation_note}{marker}"
                )
        else:
            lines.append("No models with API keys configured.")
            lines.append("Run: kt login <provider>")
        lines.append("")
        lines.append(
            "Switch: /model <provider>/<name>  "
            "(e.g. /model codex/gpt-5.4, /model openai/gpt-5.4@reasoning=high)"
        )

        return UserCommandResult(
            output="\n".join(lines),
            data=ui_select(
                "Switch Model",
                [
                    {
                        "value": _identifier(e),
                        "label": _identifier(e),
                        "model": e["model"],
                        "provider": e.get("login_provider", ""),
                        "context": f"{e.get('max_context', 0) // 1000}k",
                        "variation_groups": e.get("variation_groups", {}),
                        "selected": _is_current(e),
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
