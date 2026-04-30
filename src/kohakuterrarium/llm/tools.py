"""
Utilities for building native tool schemas from the registry.

Converts registered Tool instances into ToolSchema objects suitable
for OpenAI-compatible native function calling.
"""

from typing import Any

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import ToolSchema

# Tool parameter schemas live in a standalone module so the
# (data-only) catalogue can grow past the file-size guard without
# fragmenting the dispatch logic that lives here. See
# ``tool_schemas.py`` for the per-tool schema map.
from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def build_tool_schemas(registry: Registry) -> list[ToolSchema]:
    """
    Build native tool schemas from registered tools.

    Uses builtin schemas for known tools, falls back to tool's
    get_parameters_schema() method, then to a generic schema.

    Provider-native tools (``tool.is_provider_native == True``) are
    **skipped** — they do not represent callable functions; the
    provider translates them into its own wire-format tool spec
    instead. See :func:`build_provider_native_tools`.

    Args:
        registry: Registry containing registered tools

    Returns:
        List of ToolSchema ready for the OpenAI tools API
    """
    schemas: list[ToolSchema] = []

    for name in registry.list_tools():
        info = registry.get_tool_info(name)
        if not info:
            continue

        tool = registry.get_tool(name)
        if tool is not None and getattr(tool, "is_provider_native", False):
            continue

        # 1. Check builtin schemas first (most accurate)
        params = _BUILTIN_SCHEMAS.get(name)

        # 2. Try tool's own schema method
        if not params:
            tool = registry.get_tool(name)
            if tool and hasattr(tool, "get_parameters_schema"):
                try:
                    params = tool.get_parameters_schema() or {}  # type: ignore
                except Exception as e:
                    logger.warning(
                        "Failed to get parameters schema",
                        tool_name=name,
                        error=str(e),
                    )

        # 3. Generic fallback
        if not params:
            params = {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Input content for the tool",
                    }
                },
            }

        # Add run_in_background option to all tools
        if "properties" in params:
            params = dict(params)  # don't mutate builtin schemas
            props = dict(params.get("properties", {}))
            props["run_in_background"] = {
                "type": "boolean",
                "description": "If true, run in background. Results delivered later, not immediately.",
            }
            params["properties"] = props

        schemas.append(
            ToolSchema(
                name=name,
                description=info.description,
                parameters=params,
            )
        )

    # Also include sub-agents as callable functions
    for name in registry.list_subagents():
        subagent = registry.get_subagent(name)
        desc = (
            getattr(subagent, "description", f"Sub-agent: {name}")
            if subagent
            else f"Sub-agent: {name}"
        )
        schemas.append(
            ToolSchema(
                name=name,
                description=desc,
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Task description for the sub-agent",
                        },
                        "run_in_background": {
                            "type": "boolean",
                            "description": (
                                "If true (default), run in background — result "
                                "delivered later. If false, block and wait for "
                                "the sub-agent to finish before continuing."
                            ),
                        },
                    },
                    "required": ["task"],
                },
            )
        )

    logger.debug(
        "Built tool schemas",
        count=len(schemas),
        tools=[s.name for s in schemas],
    )
    return schemas


def build_provider_native_tools(registry: Registry) -> list[Any]:
    """Return the registered tools whose ``is_provider_native`` flag is set.

    The controller passes this list to the LLM provider as a separate
    argument (see ``BaseLLMProvider.chat``). Each provider translates
    the entries into its own built-in tool spec via
    ``translate_provider_native_tool``, or ignores them if unsupported.
    """
    out: list[Any] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        if tool is not None and getattr(tool, "is_provider_native", False):
            out.append(tool)
    return out
