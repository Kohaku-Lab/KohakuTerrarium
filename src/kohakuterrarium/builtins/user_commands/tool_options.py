"""``/tool_options`` slash command.

In-session control surface for provider-native tool option overrides.
Operates on the current ``context.agent``'s ``native_tool_options``
helper (see :class:`kohakuterrarium.core.agent_native_tools.NativeToolOptions`).

Usage forms (parsed from the slash-command argument string)::

    /tool_options                           # show all tools + values + schemas
    /tool_options <tool>                    # show one tool's current overrides
    /tool_options <tool> <key>=<value> ...  # set / override values
    /tool_options <tool> --reset            # clear overrides for this tool

Values are parsed permissively: bare strings, ``"`` / ``'`` quoted strings,
JSON literals (numbers, ``true``/``false``/``null``, lists, objects).
Strict-enum schema entries reject values outside their allowed set;
free-form ``string``/``int``/``float``/``bool`` accept any cast-compatible
value.
"""

import shlex
from typing import Any

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("tool_options")
class ToolOptionsCommand(BaseUserCommand):
    name = "tool_options"
    aliases = ["tool-options", "tooloptions"]
    description = (
        "View or override provider-native tool options for this session "
        "(e.g. /tool_options image_gen size=2048x2048 quality=high)"
    )
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")
        helper = getattr(agent, "native_tool_options", None)
        if helper is None:
            return UserCommandResult(
                error="This agent does not expose native_tool_options.",
            )

        try:
            tokens = shlex.split(args or "")
        except ValueError as exc:
            return UserCommandResult(error=f"Failed to parse arguments: {exc}")

        if not tokens:
            return UserCommandResult(output=_render_overview(agent))

        tool_name = tokens[0]
        schema = _schema_for(agent, tool_name)
        if schema is None:
            return UserCommandResult(
                error=(
                    f"Tool {tool_name!r} is not a registered provider-native "
                    "tool on this agent."
                )
            )

        rest = tokens[1:]
        if not rest:
            return UserCommandResult(
                output=_render_one_tool(agent, tool_name, helper, schema),
            )

        if any(t in {"--reset", "-r", "reset"} for t in rest):
            try:
                helper.set(tool_name, {})
            except ValueError as exc:
                return UserCommandResult(error=str(exc))
            return UserCommandResult(output=f"Reset {tool_name} options.")

        try:
            updates = _parse_assignments(rest)
        except ValueError as exc:
            return UserCommandResult(error=str(exc))

        merged = dict(helper.get(tool_name))
        for key, raw in updates.items():
            merged[key] = raw

        try:
            applied = helper.set(tool_name, merged)
        except ValueError as exc:
            return UserCommandResult(error=str(exc))
        if not applied:
            return UserCommandResult(output=f"Cleared {tool_name} options.")
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(applied.items()))
        return UserCommandResult(output=f"Set {tool_name}: {rendered}")


# ── Helpers ────────────────────────────────────────────────────────


def _schema_for(agent: Any, tool_name: str) -> dict[str, Any] | None:
    registry = getattr(agent, "registry", None)
    if registry is None:
        return None
    tool = registry.get_tool(tool_name)
    if tool is None or not getattr(tool, "is_provider_native", False):
        return None
    schema_fn = getattr(type(tool), "provider_native_option_schema", None)
    if not callable(schema_fn):
        return {}
    try:
        return schema_fn() or {}
    except Exception:
        return {}


def _parse_assignments(tokens: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tok in tokens:
        if "=" not in tok:
            raise ValueError(f"Expected key=value, got {tok!r}")
        key, _, raw = tok.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Missing key in {tok!r}")
        pairs[key] = raw
    return pairs


def _render_overview(agent: Any) -> str:
    lines: list[str] = []
    registry = getattr(agent, "registry", None)
    if registry is None:
        return "No registry available."
    helper = getattr(agent, "native_tool_options", None)
    for name in sorted(registry.list_tools()):
        tool = registry.get_tool(name)
        if tool is None or not getattr(tool, "is_provider_native", False):
            continue
        schema = _schema_for(agent, name) or {}
        if not schema:
            continue
        values = helper.get(name) if helper else {}
        summary = (
            ", ".join(f"{k}={v}" for k, v in sorted(values.items()))
            if values
            else "(defaults)"
        )
        lines.append(f"{name}: {summary}")
    if not lines:
        return "No provider-native tools with editable options on this agent."
    lines.append("")
    lines.append("Edit with: /tool_options <tool> key=value …  (or --reset)")
    return "\n".join(lines)


def _render_one_tool(
    agent: Any, tool_name: str, helper: Any, schema: dict[str, Any]
) -> str:
    values = helper.get(tool_name) if helper else {}
    out: list[str] = [f"Tool: {tool_name}"]
    if not schema:
        out.append("  (no editable options)")
        return "\n".join(out)
    out.append("  options:")
    for key, spec in schema.items():
        kind = spec.get("type", "string")
        choices = spec.get("values") or spec.get("suggestions") or []
        choice_hint = (
            f"  [{kind}: {', '.join(str(v) for v in choices)}]"
            if choices
            else f"  [{kind}]"
        )
        current = values.get(key, spec.get("default"))
        out.append(f"    {key} = {current!r}{choice_hint}")
    out.append("")
    out.append(
        "  Edit: /tool_options " + tool_name + " key=value …  (--reset to clear)"
    )
    return "\n".join(out)
