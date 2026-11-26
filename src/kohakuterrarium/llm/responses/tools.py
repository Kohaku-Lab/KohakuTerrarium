"""Responses-API tool wire fields: function tools and replay declarations."""

from typing import Any

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.modules.tool.request_replay import validate_request_replay


def function_tool(schema: ToolSchema) -> dict[str, Any]:
    """Build the non-strict Responses-API function tool for a framework tool schema.

    ``strict`` is sent explicitly because the API's default is true.
    """
    return {
        "type": "function",
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
        "strict": False,
    }


def prepare_request_tools(body: dict) -> tuple[dict, list[str]]:
    """Copy the final tool list, strip replay metadata, and identify blockers."""
    body = dict(body)
    blocked = []
    if body.get("tools") is None:
        return body, blocked
    tools = []
    for tool in body["tools"]:
        if isinstance(tool, dict):
            tool = dict(tool)
            policy = validate_request_replay(tool.pop("request_replay", "allow"))
            if policy == "forbid" and tool.get("type") != "function":
                blocked.append(str(tool.get("name") or tool.get("type") or "unknown"))
        tools.append(tool)
    body["tools"] = tools
    return body, blocked
