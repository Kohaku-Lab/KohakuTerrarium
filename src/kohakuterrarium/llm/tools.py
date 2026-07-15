"""
Build function schemas and provider-native tool lists from the registry.
"""

from typing import Any

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import ToolSchema

# Built-in schemas remain centralized so registry dispatch stays provider-agnostic.
from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def build_tool_schemas(registry: Registry) -> list[ToolSchema]:
    """Build callable schemas, excluding tools translated natively by providers."""
    schemas: list[ToolSchema] = []

    for name in registry.list_tools():
        info = registry.get_tool_info(name)
        if not info:
            continue

        tool = registry.get_tool(name)
        if tool is not None and getattr(tool, "is_provider_native", False):
            continue

        params = _BUILTIN_SCHEMAS.get(name)

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

        # Copy before adding framework execution controls to shared schemas.
        if "properties" in params:
            params = dict(params)
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
    """Return registered tools that providers translate into native wire schemas."""
    out: list[Any] = []
    for name in registry.list_tools():
        tool = registry.get_tool(name)
        if tool is not None and getattr(tool, "is_provider_native", False):
            out.append(tool)
    return out
