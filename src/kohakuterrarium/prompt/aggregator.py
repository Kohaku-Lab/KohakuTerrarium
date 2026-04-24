"""
Prompt aggregation - build system prompts from components.

Supports two skill modes:
1. Dynamic: Model uses [/info] to read tool docs on demand (less tokens)
2. Static: All tool docs included in system prompt (more context upfront)

Configurable via agent config: skill_mode: "dynamic" | "static"

Framework-hint prose (output model + execution model blocks) is sourced
from :mod:`kohakuterrarium.prompt.framework_hints`. Harness authors override
it per-creature via ``AgentConfig.framework_hint_overrides`` or per-package
via ``framework_hints:`` in ``kohaku.yaml``. Canonical override keys:

- ``framework.output_model``
- ``framework.execution_model.dynamic``
- ``framework.execution_model.static``
- ``framework.execution_model.native``

An empty string override means "omit that block entirely".
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
    """Build format-aware calling syntax header."""
    fmt = _get_tool_call_format(tool_format)
    generic = format_tool_call_example(
        fmt, "function_name", {"arg": "value"}, "content here"
    )
    return f"## Calling Functions\n\nAll functions (tools and sub-agents) use this format:\n\n```\n{generic}\n```"


def _build_command_hints(tool_format: str) -> str:
    """Build format-aware command hints (info, jobs, wait)."""
    fmt = _get_tool_call_format(tool_format)
    info_ex = format_tool_call_example(fmt, "info", body="tool_name")
    jobs_ex = format_tool_call_example(fmt, "jobs")
    wait_ex = format_tool_call_example(fmt, "wait", body="job_id")

    return (
        "## Commands\n\n"
        f"- Read docs: `{info_ex}`\n"
        f"- List jobs: `{jobs_ex}`\n"
        f"- Wait for job: `{wait_ex}`\n\n"
        "Sub-agents run in background by default. Results arrive automatically.\n"
        "Use wait only if you must block until a specific job finishes."
    )


def _build_dynamic_hints(
    registry: Registry | None = None,
    tool_format: str = "bracket",
    overrides: dict[str, str] | None = None,
) -> str:
    """Build framework hints with examples from actual registered tools."""
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
    """Build static framework hints with examples from actual registered tools."""
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
    """Build hints for native tool calling mode (no syntax examples)."""
    block = get_framework_hint(HINT_EXECUTION_MODEL_NATIVE, overrides)
    return block.strip() if block else ""


def _get_tool_call_format(tool_format: str) -> ToolCallFormat:
    """Resolve tool_format string to ToolCallFormat instance."""
    match tool_format:
        case "xml":
            return XML_FORMAT
        case _:
            return BRACKET_FORMAT


def _build_tool_examples(
    registry: Registry | None, tool_format: str = "bracket"
) -> str:
    """Generate call examples from actual registered tools and sub-agents.

    Examples are generated from the configured ToolCallFormat,
    so they work correctly for bracket, xml, or any custom format.
    """
    if not registry:
        return ""

    fmt = _get_tool_call_format(tool_format)
    examples: list[str] = []
    tool_names = set(registry.list_tools())
    subagent_names = set(registry.list_subagents())

    # Pick representative tools to show
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

    # Sub-agent example
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
) -> str:
    """
    Build complete system prompt from components.

    Args:
        base_prompt: Base system prompt (can contain Jinja2 templates)
        registry: Registry with registered tools
        include_tools: Include tool list in prompt
        include_hints: Include framework command hints
        skill_mode: "dynamic" (use [/info]) or "static" (full docs in prompt)
        tool_format: Tool calling format — "bracket", "xml", or "native".
                     Native mode skips calling syntax examples (API handles it).
        known_outputs: Set of available named output targets (e.g., {"discord"})
        channels: Channel info for prompt injection (list of dicts with
                  name, type, description). Auto-detected from session if None.
        extra_context: Extra variables for template rendering
        framework_hint_overrides: Map of canonical hint key -> replacement
            prose. See :mod:`kohakuterrarium.prompt.framework_hints` for the
            recognised keys. Empty string = omit that block entirely; unknown
            keys are ignored with a warning.

    Returns:
        Complete system prompt
    """
    parts = []

    # Render base prompt with any template variables
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

    # Add tool documentation based on skill_mode
    if registry and include_tools and "{{ tools }}" not in base_prompt:
        if skill_mode == "static":
            # Static mode: include full documentation
            full_docs = _build_full_tool_docs(registry)
            if full_docs:
                parts.append(full_docs)
        else:
            # Dynamic mode: only names + descriptions
            tools_list = _build_tools_list(registry)
            if tools_list:
                parts.append(tools_list)

    # Add per-tool prompt contributions (Cluster 5 / E.1).
    # Sits between the tool list and the framework hints; gated by
    # ``include_tools`` because a caller skipping the list likely does
    # not want per-tool prose either.
    if registry and include_tools:
        guidance = build_tool_guidance_section(registry)
        if guidance:
            parts.append(guidance)

    # Add procedural-skill index (Cluster 4 / D.2). Budget-gated so a
    # huge personal skill library doesn't swell the prompt; overflow
    # skills remain reachable via explicit `info` / `skill` tool calls.
    if skill_registry is not None:
        skill_index = build_skill_index(
            skill_registry, budget_bytes=skill_index_budget_bytes
        )
        if skill_index:
            parts.append(skill_index)

    # Add channel communication hints (when channel tools are registered)
    if registry and include_hints:
        hint_ctx = dict(extra_context or {})
        if channels is not None:
            hint_ctx["channels"] = channels
        channel_hints = _build_channel_hints(
            registry, hint_ctx, tool_format=tool_format
        )
        if channel_hints:
            parts.append(channel_hints)

    # Add framework hints (different for each mode)
    if include_hints:
        # Build output model section with available outputs
        # (skip for native mode — outputs are also API-driven)
        if tool_format != "native":
            output_hints = _build_output_hints(
                known_outputs, overrides=framework_hint_overrides
            )
            if output_hints:
                parts.append(output_hints)

        # Add function calling hints
        # Native mode: skip syntax examples entirely (API handles formatting)
        # Bracket/XML/custom: show format-appropriate examples
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
    """Build output model hints with available named outputs.

    Honors ``framework.output_model`` overrides. If an override is provided,
    its prose is used verbatim (no ``.format(...)`` is attempted, because a
    custom override is unlikely to contain the ``{named_outputs_section}``
    placeholder). An empty-string override suppresses the block entirely.
    """
    template = get_framework_hint(HINT_OUTPUT_MODEL, overrides)
    if template is None or template == "":
        logger.debug("Output-model block suppressed (empty override)")
        return ""

    # Only the default template contains ``{named_outputs_section}``; a user
    # override is treated as plain prose and returned as-is.
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
    """Build channel communication hints for standalone agents.

    For terrarium creatures, the topology prompt (build_channel_topology_prompt)
    provides the main channel guidance. This function only adds hints for
    standalone agents or agents with send_message/wait_channel tools.
    """
    tool_names = set(registry.list_tools())
    has_send = "send_message" in tool_names

    if not has_send:
        return ""

    # If channel topology was already injected (terrarium creature),
    # skip the generic hints -- topology prompt is more specific.
    channels: list[dict[str, str]] = []
    if extra_context and "channels" in extra_context:
        channels = extra_context["channels"]

    # If there are channels, the topology prompt already covers them.
    # Only add generic hints for standalone agents with no channel config.
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
    """Build a concise tool list with names and one-line descriptions."""
    tool_names = registry.list_tools()
    subagent_names = registry.list_subagents()

    if not tool_names and not subagent_names:
        return ""

    lines = ["## Available Functions", ""]

    # Tools
    if tool_names:
        lines.append("**Tools:**")
        for name in tool_names:
            info = registry.get_tool_info(name)
            description = info.description if info else "No description"
            lines.append(f"- `{name}`: {description}")
        lines.append("")

    # Sub-agents
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
    """Build full documentation for all tools and sub-agents (static mode)."""
    tool_names = registry.list_tools()
    subagent_names = registry.list_subagents()

    if not tool_names and not subagent_names:
        return ""

    parts = ["## Function Documentation", ""]

    # Get tool docs
    tool_docs = get_all_tool_docs(tool_names)
    for name in tool_names:
        doc = tool_docs.get(name)
        if doc:
            parts.append(doc)
            parts.append("")
        else:
            # Fallback to basic info
            info = registry.get_tool_info(name)
            if info:
                parts.append(f"### {name}\n{info.description}")
                parts.append("")

    # Get sub-agent docs
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
    """
    Build a context message for the controller.

    Args:
        events_content: Formatted event content
        job_status: Optional job status section

    Returns:
        Formatted context message
    """
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
    """
    Build system prompt using plugin architecture.

    Plugins are sorted by priority and their content is appended
    after the base prompt.

    Args:
        base_prompt: Base system prompt (agent personality/guidelines)
        plugins: List of plugins to use (default: tool_list + framework_hints)
        registry: Registry with registered tools
        working_dir: Working directory for context
        agent_path: Agent folder path
        extra_context: Extra variables for template rendering

    Returns:
        Complete system prompt
    """
    # Use default plugins if none provided
    if plugins is None:
        plugins = get_default_plugins()

    # Create context for plugins
    context = PluginContext(
        registry=registry,
        working_dir=working_dir or Path.cwd(),
        agent_path=agent_path,
        extra=extra_context or {},
    )

    # Start with rendered base prompt
    template_vars = extra_context or {}
    rendered_base = render_template_safe(base_prompt, **template_vars)
    parts = [rendered_base]

    # Sort plugins by priority and collect content
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
