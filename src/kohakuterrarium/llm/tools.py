"""
Build function schemas and provider-native tool lists from the registry.

Framework semantics are stated once in the system prompt, never repeated per
schema: ``run_in_background`` appears only on tools that declare
``supports_background``, and sub-agent isolation is explained in the
execution-model block rather than in every ``task`` parameter.
"""

from typing import Any

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.modules.tool.doc_mode import (
    DOC_MODE_BRIEF,
    DEFAULT_DOC_MODE,
    resolve_doc_mode,
)

# Built-in schemas remain centralized so registry dispatch stays provider-agnostic.
from kohakuterrarium.llm.tool_schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_BACKGROUND_ARG = {
    "type": "boolean",
    "description": "Run without waiting; the result arrives in a later turn.",
}

_SUBAGENT_BACKGROUND_ARG = {
    "type": "boolean",
    "description": "Default true. Set false to wait for the result before continuing.",
}

_GENERIC_PARAMS = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "description": "Input content for the tool"}
    },
}


def _strip_descriptions(schema: Any) -> Any:
    """Return a copy of a JSON schema with every ``description`` removed."""
    if isinstance(schema, dict):
        return {
            key: _strip_descriptions(value)
            for key, value in schema.items()
            if key != "description"
        }
    if isinstance(schema, list):
        return [_strip_descriptions(item) for item in schema]
    return schema


def _tool_parameters(registry: Registry, name: str) -> dict:
    """Resolve a tool's parameter schema from the builtin table or the tool."""
    params = _BUILTIN_SCHEMAS.get(name)
    if params:
        return params

    tool = registry.get_tool(name)
    if tool and hasattr(tool, "get_parameters_schema"):
        try:
            params = tool.get_parameters_schema() or {}  # type: ignore
        except Exception as e:
            logger.warning(
                "Failed to get parameters schema", tool_name=name, error=str(e)
            )
    return params or _GENERIC_PARAMS


def build_tool_schemas(
    registry: Registry,
    *,
    tool_doc_mode: str = DEFAULT_DOC_MODE,
) -> list[ToolSchema]:
    """Build callable schemas, excluding tools translated natively by providers."""
    schemas: list[ToolSchema] = []

    for name in registry.list_tools():
        info = registry.get_tool_info(name)
        if not info:
            continue

        tool = registry.get_tool(name)
        if tool is not None and getattr(tool, "is_provider_native", False):
            continue

        params = _tool_parameters(registry, name)

        if resolve_doc_mode(tool, tool_doc_mode) == DOC_MODE_BRIEF:
            params = _strip_descriptions(params)

        # Copy before adding framework execution controls to shared schemas.
        if "properties" in params and getattr(tool, "supports_background", False):
            params = dict(params)
            params["properties"] = {
                **params.get("properties", {}),
                "run_in_background": dict(_BACKGROUND_ARG),
            }

        schemas.append(
            ToolSchema(name=name, description=info.description, parameters=params)
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
                            "description": "Complete, self-contained task description.",
                        },
                        "run_in_background": dict(_SUBAGENT_BACKGROUND_ARG),
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
