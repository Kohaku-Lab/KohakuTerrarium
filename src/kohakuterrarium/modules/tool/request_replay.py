"""Explicit permission to resubmit model requests containing a tool."""

from typing import Any, Literal

RequestReplay = Literal["allow", "forbid"]


def validate_request_replay(value: Any) -> RequestReplay:
    """Validate an explicit tool replay declaration."""
    if value not in ("allow", "forbid"):
        raise ValueError("request_replay must be 'allow' or 'forbid'")
    return value


def tool_request_replay(tool: Any) -> RequestReplay:
    """Resolve configuration overrides before the implementation default."""
    override = getattr(getattr(tool, "config", None), "request_replay", None)
    return validate_request_replay(
        override if override is not None else getattr(tool, "request_replay", "allow")
    )
