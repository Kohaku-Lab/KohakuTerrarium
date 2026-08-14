"""Validate runtime-mutable options declared by ordinary tools."""

from typing import Any


class ToolOptionError(ValueError):
    """Raised when a runtime tool option violates its schema."""


def validate_tool_options(
    tool_name: str,
    values: dict[str, Any],
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate supported primitive values against a tool option schema."""
    if not isinstance(values, dict):
        raise ToolOptionError("values must be an object")

    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if value in (None, ""):
            continue
        if key not in schema:
            raise ToolOptionError(f"Unknown option {key!r} for {tool_name!r}")
        spec = schema[key] or {}
        cleaned[key] = _coerce_value(tool_name, key, value, spec)
    return cleaned


def _coerce_value(tool_name: str, key: str, value: Any, spec: dict[str, Any]) -> Any:
    kind = str(spec.get("type", "string"))
    if kind == "enum":
        if not isinstance(value, str):
            raise ToolOptionError(f"{key!r} must be a string enum value")
        allowed = [str(v) for v in (spec.get("values") or [])]
        if value not in allowed:
            raise ToolOptionError(
                f"{key!r} value {value!r} must be one of: {', '.join(allowed)}"
            )
        disabled = spec.get("disabled_values") or {}
        if value in disabled:
            reason = str(disabled[value] or "This value is currently unavailable")
            raise ToolOptionError(reason)
        return value
    if kind == "string":
        if not isinstance(value, str):
            raise ToolOptionError(f"{key!r} must be a string")
        maximum = int(spec.get("max_length", 128))
        if len(value) > maximum:
            raise ToolOptionError(f"{key!r} is too long")
        return value
    if kind == "int":
        if isinstance(value, bool):
            raise ToolOptionError(f"{key!r} must be an integer")
        try:
            coerced: int | float = int(value)
        except (TypeError, ValueError):
            raise ToolOptionError(f"{key!r} must be an integer")
        _validate_bounds(key, coerced, spec)
        return coerced
    if kind == "float":
        if isinstance(value, bool):
            raise ToolOptionError(f"{key!r} must be a number")
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            raise ToolOptionError(f"{key!r} must be a number")
        _validate_bounds(key, coerced, spec)
        return coerced
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        raise ToolOptionError(f"{key!r} must be a boolean")
    raise ToolOptionError(f"Unsupported option type {kind!r} for {key!r}")


def _validate_bounds(key: str, value: int | float, spec: dict[str, Any]) -> None:
    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and value < minimum:
        raise ToolOptionError(f"{key!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ToolOptionError(f"{key!r} must be <= {maximum}")
