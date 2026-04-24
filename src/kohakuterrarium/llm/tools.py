"""
Utilities for building native tool schemas from the registry.

Converts registered Tool instances into ToolSchema objects suitable
for OpenAI-compatible native function calling.
"""

from typing import Any

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Known parameter schemas for builtin tools.
# Maps tool name → OpenAI function parameters schema.
_BUILTIN_SCHEMAS: dict[str, dict] = {
    "bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "type": {
                "type": "string",
                "description": "Shell type (default: bash). Options: bash, zsh, sh, fish, pwsh",
            },
        },
        "required": ["command"],
    },
    "python": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"},
        },
        "required": ["code"],
    },
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "offset": {"type": "integer", "description": "Line offset (optional)"},
            "limit": {"type": "integer", "description": "Max lines (optional)"},
        },
        "required": ["path"],
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["path", "content"],
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old": {
                "type": "string",
                "description": "Exact text to find (search/replace mode)",
            },
            "new": {
                "type": "string",
                "description": "Replacement text (search/replace mode)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
            },
            "diff": {
                "type": "string",
                "description": "Unified diff content (diff mode, alternative to old/new)",
            },
        },
        "required": ["path"],
    },
    "multi_edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "edits": {
                "type": "array",
                "description": "Ordered list of search/replace edits applied to one file",
                "items": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string", "description": "Exact text to find"},
                        "new": {"type": "string", "description": "Replacement text"},
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences for this edit (default false)",
                        },
                    },
                    "required": ["old", "new"],
                },
            },
            "strict": {
                "type": "boolean",
                "description": "If true (default), any failed edit aborts the whole operation and leaves the file unchanged",
            },
            "best_effort": {
                "type": "boolean",
                "description": "If true, keep going after failed edits. Cannot be combined with strict=true",
            },
        },
        "required": ["path", "edits"],
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
            "path": {"type": "string", "description": "Base directory (optional)"},
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
        "required": ["pattern"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search"},
            "path": {"type": "string", "description": "Directory or file to search"},
            "glob": {"type": "string", "description": "File glob filter (optional)"},
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
        "required": ["pattern"],
    },
    "tree": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path"},
            "depth": {"type": "integer", "description": "Max depth (default 3)"},
            "limit": {
                "type": "integer",
                "description": "Max output lines (default 100, 0 = unlimited)",
            },
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
    },
    "scratchpad": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "delete", "list"],
                "description": "Operation to perform",
            },
            "key": {"type": "string", "description": "Key name"},
            "value": {"type": "string", "description": "Value (for set)"},
        },
        "required": ["action"],
    },
    "send_message": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel name"},
            "message": {"type": "string", "description": "Message content"},
            "reply_to": {"type": "string", "description": "Message ID to reply to"},
        },
        "required": ["channel", "message"],
    },
    "info": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the tool, sub-agent, or procedural skill to get documentation for",
            },
        },
        "required": ["name"],
    },
    "skill": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the procedural skill to invoke",
            },
            "arguments": {
                "type": "string",
                "description": "Optional user/task arguments to pass along to the skill",
            },
        },
        "required": ["name"],
    },
    "search_memory": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (keywords or natural language)",
            },
            "mode": {
                "type": "string",
                "enum": ["fts", "semantic", "hybrid", "auto"],
                "description": "Search mode (default: auto)",
            },
            "k": {
                "type": "integer",
                "description": "Max results (default: 5)",
            },
        },
        "required": ["query"],
    },
    "web_fetch": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch and read",
            },
        },
        "required": ["url"],
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default: 10)",
            },
        },
        "required": ["query"],
    },
    "ask_user": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to ask the user"},
        },
        "required": ["question"],
    },
    "json_read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "JSON file path"},
            "query": {"type": "string", "description": "JMESPath query (optional)"},
        },
        "required": ["path"],
    },
    "json_write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "JSON file path"},
            "content": {"type": "string", "description": "JSON content to write"},
        },
        "required": ["path", "content"],
    },
}


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
