"""Separate framework replay declarations from provider tool wire fields."""

from kohakuterrarium.modules.tool.request_replay import validate_request_replay


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
