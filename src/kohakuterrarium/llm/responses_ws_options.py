"""Validated resource and liveness settings for Responses WebSocket connections."""

import math
from typing import Any

from kohakuterrarium.llm.responses_ws import DEFAULT_FALLBACK_AFTER

FRAMEWORK_KNOBS = frozenset(
    {
        "disable_prompt_caching",
        "websocket_mode",
        "websocket_connection_options",
        "websocket_max_message_bytes",
        "websocket_fallback_after",
        "request_max_bytes",
        "responses_reasoning_replay",
    }
)

# Whole-request byte target that inline images are compressed to fit.
DEFAULT_REQUEST_MAX_BYTES = 15 * 1024 * 1024


def _non_negative_int(
    extra: dict[str, Any], name: str, default: int | None
) -> int | None:
    value = extra.get(name, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")
    return value


def build_ws_session_options(extra_body: dict[str, Any]) -> dict[str, Any]:
    """Return the frame ceiling and sticky-fallback threshold for a WS session.

    ``websocket_fallback_after: 0`` keeps retrying WS on every turn.
    """
    return {
        "max_message_bytes": _non_negative_int(
            extra_body, "websocket_max_message_bytes", None
        )
        or None,
        "fallback_after": _non_negative_int(
            extra_body, "websocket_fallback_after", DEFAULT_FALLBACK_AFTER
        )
        or 0,
    }


def request_max_bytes(
    extra_body: dict[str, Any], default: int = DEFAULT_REQUEST_MAX_BYTES
) -> int | None:
    """Return the request byte target for image compression; ``0``/null disables it."""
    return _non_negative_int(extra_body, "request_max_bytes", default) or None


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
        "compression": "deflate",
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
