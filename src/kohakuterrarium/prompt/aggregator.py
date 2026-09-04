"""Compose system prompts from tools, skills, plugins, and framework hints.

Every framework section carries an explicit gate. A block whose subject does not
exist in this runtime — call syntax for a native creature, graph prose for a solo
one — is not emitted at all, because an inapplicable block is worse than a
missing one: it teaches the model something false.

Framework-hint overrides may replace canonical blocks; an empty override omits
that block entirely.
"""

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
    HINT_CALL_SYNTAX,
    HINT_EXECUTION_MODEL,
    HINT_OUTPUT_MODEL,
    HINT_UNTRUSTED_CONTENT,
    call_discipline,
    get_framework_hint,
    is_default_hint,
)
from kohakuterrarium.modules.plugin.base import PluginContext as RuntimePluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.tool.doc_mode import (
    DOC_MODE_FULL,
    DEFAULT_DOC_MODE,
    resolve_doc_mode,
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

    if subagent_names:
        first_sa = sorted(subagent_names)[0]
        ex = format_tool_call_example(fmt, first_sa, body="describe the task here")
        examples.append(f"```\n{ex}\n```")

    return "\n\n".join(examples)


def _build_call_syntax(
    registry: Registry | None,
    tool_format: str,
    overrides: dict[str, str] | None = None,
) -> str:
    """Render the call-syntax block for text tool-call formats.

    Examples are generated from the active format definition, so they cannot
    drift from what the parser actually accepts.
    """
    template = get_framework_hint(HINT_CALL_SYNTAX, overrides)
    if not template:
        return ""
    if not is_default_hint(HINT_CALL_SYNTAX, template):
        return template.strip()

    fmt = _get_tool_call_format(tool_format)
    examples = _build_tool_examples(registry, tool_format=tool_format)
    return template.format(
        format_example=format_tool_call_example(
            fmt, "function_name", {"arg": "value"}, "content here"
        ),
        examples=f"\nExamples:\n\n{examples}\n" if examples else "",
        info_example=format_tool_call_example(fmt, "info", body="tool_name"),
        jobs_example=format_tool_call_example(fmt, "jobs"),
        wait_example=format_tool_call_example(fmt, "wait", body="job_id"),
    ).strip()


def _build_execution_model(
    tool_format: str,
    overrides: dict[str, str] | None = None,
) -> str:
    """Render the single execution-model block for any tool format."""
    template = get_framework_hint(HINT_EXECUTION_MODEL, overrides)
    if not template:
        return ""
    if not is_default_hint(HINT_EXECUTION_MODEL, template):
        return template.strip()
    return template.format(call_discipline=call_discipline(tool_format)).strip()


def _build_untrusted_content(overrides: dict[str, str] | None = None) -> str:
    """Render the untrusted-content block."""
    block = get_framework_hint(HINT_UNTRUSTED_CONTENT, overrides)
    return block.strip() if block else ""


def aggregate_system_prompt(
    base_prompt: str,
    registry: Registry | None = None,
    *,
    include_tools: bool = True,
    include_hints: bool = True,
    tool_doc_mode: str = DEFAULT_DOC_MODE,
    tool_format: str = "native",
    known_outputs: set[str] | None = None,
    extra_context: dict | None = None,
    framework_hint_overrides: dict[str, str] | None = None,
    skill_registry: Any | None = None,
    skill_index_budget_bytes: int = DEFAULT_SKILL_INDEX_BUDGET_BYTES,
    runtime_plugins: PluginManager | None = None,
    plugin_context: RuntimePluginContext | None = None,
) -> str:
    """Build a complete system prompt in stable component order.

    Sections are gated: the inventory is skipped when the provider already
    carries it natively, the call-syntax block only exists for text formats, and
    inline tool usage appears only for tools resolved to ``full``. Graph
    sections are owned by the terrarium layer and are not emitted here.
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

    is_native = tool_format == "native"

    if registry and include_tools and "{{ tools }}" not in base_prompt:
        # Native providers already receive name + description as schema; a
        # second copy in prose is pure duplication.
        if not is_native or tool_doc_mode == DOC_MODE_FULL:
            tools_list = _build_tools_list(registry)
            if tools_list:
                parts.append(tools_list)

        inline_docs = _build_inline_tool_docs(registry, tool_doc_mode)
        if inline_docs:
            parts.append(inline_docs)

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

    if include_hints:
        untrusted = _build_untrusted_content(framework_hint_overrides)
        if untrusted:
            parts.append(untrusted)

        if not is_native:
            syntax = _build_call_syntax(
                registry, tool_format, overrides=framework_hint_overrides
            )
            if syntax:
                parts.append(syntax)

            output_hints = _build_output_hints(
                known_outputs, overrides=framework_hint_overrides
            )
            if output_hints:
                parts.append(output_hints)

        execution = _build_execution_model(
            tool_format, overrides=framework_hint_overrides
        )
        if execution:
            parts.append(execution)

    result = "\n\n".join(parts)
    logger.debug(
        "Aggregated system prompt",
        length=len(result),
        tool_doc_mode=tool_doc_mode,
        tool_format=tool_format,
    )
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

    if not is_default_hint(HINT_OUTPUT_MODEL, template):
        return template.strip()

    if not known_outputs:
        return template.format(named_outputs_section="").strip()

    outputs_list = ", ".join(f"`{name}`" for name in sorted(known_outputs))
    first_output = sorted(known_outputs)[0]
    named_section = NAMED_OUTPUTS_SECTION_TEMPLATE.format(
        outputs_list=outputs_list,
        first_output=first_output,
    )
    return template.format(named_outputs_section=named_section).strip()


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


def _build_inline_tool_docs(registry: Registry, default_mode: str) -> str:
    """Inline the usage tier for every function resolved to ``full``.

    Only the usage tier is inlined; reference material stays behind ``info`` in
    every mode, so ``full`` costs a bounded amount per tool.
    """
    tool_names = [
        name
        for name in registry.list_tools()
        if resolve_doc_mode(registry.get_tool(name), default_mode) == DOC_MODE_FULL
    ]
    subagent_names = (
        list(registry.list_subagents()) if default_mode == DOC_MODE_FULL else []
    )

    if not tool_names and not subagent_names:
        return ""

    parts = ["## Function Documentation", ""]

    tool_docs = get_all_tool_docs(tool_names, tier="usage")
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

    subagent_docs = get_all_subagent_docs(subagent_names, tier="usage")
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

    return "\n".join(parts).rstrip()


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
