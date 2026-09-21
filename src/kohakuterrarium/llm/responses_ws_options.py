"""Validated resource and liveness settings for Responses WebSocket connections."""

import math
from typing import Any

FRAMEWORK_KNOBS = frozenset(
    {
        "disable_prompt_caching",
        "websocket_mode",
        "websocket_connection_options",
        "responses_reasoning_replay",
    }
)

_SIZE_OPTIONS = {"max_size", "max_queue", "write_limit"}
_TIMEOUT_OPTIONS = {"open_timeout", "ping_interval", "ping_timeout", "close_timeout"}


def build_websocket_connection_options(
    configured: Any, *, timeout: float | None
) -> dict[str, Any]:
    """Return a validated snapshot of provider socket settings."""
    if configured is None:
        configured = {}
    if not isinstance(configured, dict):
        raise ValueError("websocket_connection_options must be a dictionary or null")
    options = {
        "max_size": None,
        "open_timeout": timeout,
        "ping_timeout": timeout,
        **configured,
    }
    for name, value in options.items():
        if name in _SIZE_OPTIONS:
            if value is None and name != "write_limit":
                continue
            valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
            expected = "a positive integer" + (
                " or null" if name != "write_limit" else ""
            )
        elif name in _TIMEOUT_OPTIONS:
            if value is None:
                continue
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            if valid:
                try:
                    value = float(value)
                    valid = math.isfinite(value) and value > 0
                except OverflowError:
                    valid = False
            expected = "a positive finite number or null"
        elif name == "compression":
            valid = value in (None, "deflate")
            expected = '"deflate" or null'
        else:
            raise ValueError(f"Unsupported websocket_connection_options key: {name}")
        if not valid:
            raise ValueError(f"websocket_connection_options.{name} must be {expected}")
        options[name] = value
    return options
