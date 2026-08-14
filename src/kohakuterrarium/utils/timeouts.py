"""Shared timeout argument parsing helpers."""

import math
from typing import Any


def resolve_timeout_arg(
    args: dict[str, Any], default_timeout: float
) -> tuple[float, str | None]:
    """Resolve a per-call timeout, falling back to the configured default."""
    raw_timeout = args.get("timeout", default_timeout)
    if raw_timeout in (None, ""):
        raw_timeout = default_timeout
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        return 0.0, f"timeout must be numeric, got {raw_timeout!r}"
    if not math.isfinite(timeout):
        return 0.0, "timeout must be finite"
    if timeout < 0:
        return 0.0, "timeout must be >= 0"
    return timeout, None
