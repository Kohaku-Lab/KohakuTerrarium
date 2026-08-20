"""Compose system prompts from tools, skills, channels, plugins, and hints.

Framework-hint overrides may replace canonical blocks; an empty override omits
that block entirely.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.builtin_skills import get_all_subagent_docs, get_all_tool_docs
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    XML_FORMAT,
    ToolCallFormat,
    format_tool_call_example,
)
from kohakuterrarium.prompt.framework_hints import (
    HINT_EXECUTION_MODEL_DYNAMIC,
    HINT_EXECUTION_MODEL_NATIVE,
    HINT_EXECUTION_MODEL_STATIC,
    HINT_OUTPUT_MODEL,
    get_framework_hint,
)
from kohakuterrarium.modules.plugin.base import PluginContext as RuntimePluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.prompt.plugins import (
    BasePlugin,
    PluginContext,
    get_default_plugins,
)
from kohakuterrarium.prompt.template import render_template_safe
from kohakuterrarium.prompt.tool_contributions import build_tool_guidance_section
from kohakuterrarium.skills.index import (
    DEFAULT_SKILL_INDEX_BUDGET_BYTES,
    build_skill_index,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


NAMED_OUTPUTS_SECTION_TEMPLATE = """
Available: {outputs_list}

---output example---
[/output_{first_output}]Hello![output_{first_output}/]
---end example---

If you want to send to {first_output}, wrap your message exactly like above.
Without the wrapper, nothing gets sent.
"""


def _build_format_header(tool_format: str) -> str:
    """Render the configured function-call syntax header."""
    fmt = _get_tool_call_format(tool_format)
    generic = format_tool_call_example(
        fmt, "function_name", {"arg": "value"}, "content here"
    )
    return f"## Calling Functions\n\nAll functions (tools and sub-agents) use this format:\n\n```\n{generic}\n```"


def _build_command_hints(tool_format: str) -> str:
    """Render command examples in the configured call syntax."""
    fmt = _get_tool_call_format(tool_format)
    info_ex = format_tool_call_example(fmt, "info", body="tool_name")
    jobs_ex = format_tool_call_example(fmt, "jobs")
    wait_ex = format_tool_call_example(fmt, "wait", body="job_id")

    return (
        "## Commands\n\n"
        f"- Read docs: `{info_ex}`\n"
        f"- List jobs: `{jobs_ex}`\n"
        f"- Wait for job: `{wait_ex}`\n\n"
        "Starting background work does not immediately return a result or give "
        "you another turn to act. Results are normally delivered after the work "
        "finishes. Use wait only if you must block until a specific job finishes."
    )


def _build_dynamic_hints(
    registry: Registry | None = None,
    tool_format: str = "bracket",
    overrides: dict[str, str] | None = None,
) -> str:
    """Build dynamic-mode hints from registered functions."""
    parts = [_build_format_header(tool_format)]

    examples = _build_tool_examples(registry, tool_format=tool_format)
    if examples:
        parts.append("Examples:\n" + examples)

    execution_block = get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC, overrides)
    if execution_block:
        parts.append(execution_block.strip())
    parts.append(_build_command_hints(tool_format))
    return "\n\n".join(parts)


def _build_static_hints(
    registry: Registry | None = None,
    tool_format: str = "bracket",
    overrides: dict[str, str] | None = None,
) -> str:
    """Build static-mode hints from registered functions."""
    parts = [_build_format_header(tool_format)]

    examples = _build_tool_examples(registry, tool_format=tool_format)
    if examples:
        parts.append("Examples:\n" + examples)

    execution_block = get_framework_hint(HINT_EXECUTION_MODEL_STATIC, overrides)
    if execution_block:
        parts.append(execution_block.strip())
    return "\n\n".join(parts)


def _build_native_hints(
    registry: Registry | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    """Build native-call hints without textual syntax examples."""
    block = get_framework_hint(HINT_EXECUTION_MODEL_NATIVE, overrides)
    return block.strip() if block else ""


def _get_tool_call_format(tool_format: str) -> ToolCallFormat:
    """Resolve a configured format name to its parser format."""
    match tool_format:
        case "xml":
            return XML_FORMAT
        case _:
            return BRACKET_FORMAT


def _build_tool_examples(
    registry: Registry | None, tool_format: str = "bracket"
) -> str:
    """Render representative registered functions in the configured syntax."""
    if not registry:
        return ""

    fmt = _get_tool_call_format(tool_format)
    examples: list[str] = []
    tool_names = set(registry.list_tools())
    subagent_names = set(registry.list_subagents())

    if "read" in tool_names:
        ex = format_tool_call_example(fmt, "read", {"path": "file.py"})
        examples.append(f"```\n{ex}\n```")
    elif "glob" in tool_names:
        ex = format_tool_call_example(fmt, "glob", {"pattern": "**/*.py"})
        examples.append(f"```\n{ex}\n```")

    if "bash" in tool_names:
        ex = format_tool_call_example(fmt, "bash", body="ls -la")
        examples.append(f"```\n{ex}\n```")

    if "write" in tool_names:
        ex = format_tool_call_example(fmt, "write", {"path": "out.txt"}, "content here")
        examples.append(f"```\n{ex}\n```")
    elif "send_message" in tool_names:
        ex = format_tool_call_example(
            fmt, "send_message", {"channel": "inbox"}, "Hello from agent"
        )
        examples.append(f"```\n{ex}\n```")

    if subagent_names:
        first_sa = sorted(subagent_names)[0]
        ex = format_tool_call_example(fmt, first_sa, body="describe the task here")
        examples.append(f"```\n{ex}\n```")

    return "\n\n".join(examples)


def aggregate_system_prompt(
    base_prompt: str,
    registry: Registry | None = None,
    *,
    include_tools: bool = True,
    include_hints: bool = True,
    skill_mode: str = "dynamic",
    tool_format: str = "bracket",
    known_outputs: set[str] | None = None,
    channels: list[dict[str, str]] | None = None,
    extra_context: dict | None = None,
    framework_hint_overrides: dict[str, str] | None = None,
    skill_registry: Any | None = None,
    skill_index_budget_bytes: int = DEFAULT_SKILL_INDEX_BUDGET_BYTES,
    runtime_plugins: PluginManager | None = None,
    plugin_context: RuntimePluginContext | None = None,
) -> str:
    """Build a complete system prompt in stable component order.

    Dynamic mode lists functions for on-demand documentation; static mode embeds
    full documentation. Native mode omits textual calling syntax.
    """
    parts = []

    context = extra_context or {}
    if registry and include_tools:
        context["tools"] = [
            {
                "name": name,
                "description": (
                    registry.get_tool_info(name).description
                    if registry.get_tool_info(name)
                    else ""
                ),
            }
            for name in registry.list_tools()
        ]

    rendered_base = render_template_safe(base_prompt, **context)
    parts.append(rendered_base)

    if registry and include_tools and "{{ tools }}" not in base_prompt:
        if skill_mode == "static":
            full_docs = _build_full_tool_docs(registry)
            if full_docs:
                parts.append(full_docs)
        else:
            tools_list = _build_tools_list(registry)
            if tools_list:
                parts.append(tools_list)

    # Tool guidance follows inventory and obeys the same inclusion gate.
    if registry and include_tools:
        guidance = build_tool_guidance_section(registry)
        if guidance:
            parts.append(guidance)

    # Plugin guidance shares the tool-guidance position before execution hints.
    if runtime_plugins is not None and plugin_context is not None:
        for contribution in runtime_plugins.collect_prompt_contributions(
            plugin_context
        ):
            if contribution:
                parts.append(contribution)

    # The byte budget bounds prompt growth; omitted skills remain callable.
    if skill_registry is not None:
        skill_index = build_skill_index(
            skill_registry, budget_bytes=skill_index_budget_bytes
        )
        if skill_index:
            parts.append(skill_index)

    if registry and include_hints:
        hint_ctx = dict(extra_context or {})
        if channels is not None:
            hint_ctx["channels"] = channels
        channel_hints = _build_channel_hints(
            registry, hint_ctx, tool_format=tool_format
        )
        if channel_hints:
            parts.append(channel_hints)

    if include_hints:
        # Native outputs are API-driven and need no textual wrapper guidance.
        if tool_format != "native":
            output_hints = _build_output_hints(
                known_outputs, overrides=framework_hint_overrides
            )
            if output_hints:
                parts.append(output_hints)

        if tool_format == "native":
            hints = _build_native_hints(registry, overrides=framework_hint_overrides)
        elif skill_mode == "static":
            hints = _build_static_hints(
                registry,
                tool_format=tool_format,
                overrides=framework_hint_overrides,
            )
        else:
            hints = _build_dynamic_hints(
                registry,
                tool_format=tool_format,
                overrides=framework_hint_overrides,
            )
        if hints:
            parts.append(hints)

    result = "\n\n".join(parts)
    logger.debug("Aggregated system prompt", length=len(result), skill_mode=skill_mode)
    return result


def _build_output_hints(
    known_outputs: set[str] | None,
    *,
    overrides: dict[str, str] | None = None,
) -> str:
    """Build named-output guidance while honoring output-model overrides.

    Custom overrides are literal prose; an empty override suppresses the block.
    """
    template = get_framework_hint(HINT_OUTPUT_MODEL, overrides)
    if template is None or template == "":
        logger.debug("Output-model block suppressed (empty override)")
        return ""

    # Only the default template supports named-output interpolation.
    is_default_template = "{named_outputs_section}" in template
    logger.debug("Building output hints", known_outputs=known_outputs)
    if not is_default_template:
        return template.strip()

    if not known_outputs:
        logger.debug("No known outputs, using basic output model")
        return template.format(named_outputs_section="").strip()

    outputs_list = ", ".join(f"`{name}`" for name in sorted(known_outputs))
    first_output = sorted(known_outputs)[0]
    named_section = NAMED_OUTPUTS_SECTION_TEMPLATE.format(
        outputs_list=outputs_list,
        first_output=first_output,
    )
    return template.format(named_outputs_section=named_section).strip()


def _build_channel_hints(
    registry: Registry,
    extra_context: dict | None = None,
    tool_format: str = "bracket",
) -> str:
    """Build generic channel guidance only when topology guidance is absent."""
    tool_names = set(registry.list_tools())
    has_send = "send_message" in tool_names

    if not has_send:
        return ""

    channels: list[dict[str, str]] = []
    if extra_context and "channels" in extra_context:
        channels = extra_context["channels"]

    # Concrete topology guidance is more specific than these generic hints.
    if channels:
        return ""

    lines = ["## Internal Channels", ""]
    lines.append(
        "`send_message` is for communicating with other creatures through "
        "team channels, or with your own sub-agents through internal channels."
    )
    lines.append("")
    lines.append("**Usage:**")
    lines.append("- `send_message(channel, message)` -- send to a named channel")
    lines.append("")

    return "\n".join(lines)


def _build_tools_list(registry: Registry) -> str:
    """List registered tools and sub-agents with concise descriptions."""
    tool_names = registry.list_tools()
    subagent_names = registry.list_subagents()

    if not tool_names and not subagent_names:
        return ""

    lines = ["## Available Functions", ""]

    if tool_names:
        lines.append("**Tools:**")
        for name in tool_names:
            info = registry.get_tool_info(name)
            description = info.description if info else "No description"
            lines.append(f"- `{name}`: {description}")
        lines.append("")

    if subagent_names:
        lines.append("**Sub-agents:**")
        for name in subagent_names:
            subagent = registry.get_subagent(name)
            desc = (
                getattr(subagent, "description", "Sub-agent")
                if subagent
                else "Sub-agent"
            )
            lines.append(f"- `{name}`: {desc}")
        lines.append("")

    lines.append("Use the `info` tool for full documentation on any function.")

    return "\n".join(lines)


def _build_full_tool_docs(registry: Registry) -> str:
    """Embed full registered function documentation for static mode."""
    tool_names = registry.list_tools()
    subagent_names = registry.list_subagents()

    if not tool_names and not subagent_names:
        return ""

    parts = ["## Function Documentation", ""]

    tool_docs = get_all_tool_docs(tool_names)
    for name in tool_names:
        doc = tool_docs.get(name)
        if doc:
            parts.append(doc)
            parts.append("")
        else:
            info = registry.get_tool_info(name)
            if info:
                parts.append(f"### {name}\n{info.description}")
                parts.append("")

    subagent_docs = get_all_subagent_docs(subagent_names)
    for name in subagent_names:
        doc = subagent_docs.get(name)
        if doc:
            parts.append(doc)
            parts.append("")
        else:
            subagent = registry.get_subagent(name)
            desc = (
                getattr(subagent, "description", "Sub-agent")
                if subagent
                else "Sub-agent"
            )
            parts.append(f"### {name}\n{desc}")
            parts.append("")

    return "\n".join(parts)


def build_context_message(
    events_content: str,
    job_status: str | None = None,
) -> str:
    """Combine event content with optional running-job status."""
    parts = []

    if job_status:
        parts.append(f"## Running Jobs\n{job_status}")

    parts.append(events_content)

    return "\n\n".join(parts)


def aggregate_with_plugins(
    base_prompt: str,
    plugins: list[BasePlugin] | None = None,
    *,
    registry: Registry | None = None,
    working_dir: Path | None = None,
    agent_path: Path | None = None,
    extra_context: dict | None = None,
) -> str:
    """Render the base prompt and append plugin content by priority."""
    if plugins is None:
        plugins = get_default_plugins()

    context = PluginContext(
        registry=registry,
        working_dir=working_dir or Path.cwd(),
        agent_path=agent_path,
        extra=extra_context or {},
    )

    template_vars = extra_context or {}
    rendered_base = render_template_safe(base_prompt, **template_vars)
    parts = [rendered_base]

    sorted_plugins = sorted(plugins, key=lambda p: p.priority)
    for plugin in sorted_plugins:
        try:
            content = plugin.get_content(context)
            if content:
                parts.append(content)
                logger.debug(
                    "Plugin contributed content",
                    plugin=plugin.name,
                    length=len(content),
                )
        except Exception as e:
            logger.warning("Plugin failed", plugin=plugin.name, error=str(e))

    result = "\n\n".join(parts)
    logger.debug(
        "Aggregated system prompt with plugins",
        length=len(result),
        plugin_count=len(plugins),
    )
    return result
