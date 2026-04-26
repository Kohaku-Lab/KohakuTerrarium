"""Validation helpers for provider-native tool option overrides."""

import re
from typing import Any

_SIZE_RE = re.compile(r"^(?P<w>\d{1,5})x(?P<h>\d{1,5})$")
_MAX_STRING_LENGTH = 128
_MIN_IMAGE_SIDE = 64
_MAX_IMAGE_SIDE = 4096


class NativeToolOptionError(ValueError):
    """Raised when provider-native option input is invalid."""


def validate_native_tool_options(
    tool_name: str,
    values: dict[str, Any],
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate and coerce provider-native option overrides.

    Rejects unknown keys, invalid enum members, wrong primitive types,
    overly-long strings, and image-generation ``size`` values outside a
    conservative ``64..4096`` pixel side range.
    """
    if not isinstance(values, dict):
        raise NativeToolOptionError("values must be an object")

    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if value in (None, ""):
            continue
        if key not in schema:
            raise NativeToolOptionError(f"Unknown option {key!r} for {tool_name!r}")
        spec = schema[key] or {}
        cleaned[key] = _coerce_value(tool_name, key, value, spec)
    return cleaned


def _coerce_value(tool_name: str, key: str, value: Any, spec: dict[str, Any]) -> Any:
    kind = str(spec.get("type", "string"))
    if kind == "enum":
        allowed = [str(v) for v in (spec.get("values") or [])]
        if value is None:
            return None
        if not isinstance(value, str):
            raise NativeToolOptionError(f"{key!r} must be a string enum value")
        if value not in allowed:
            raise NativeToolOptionError(f"{key!r} must be one of: {', '.join(allowed)}")
        return value
    if kind == "string":
        if value is None:
            return None
        if not isinstance(value, str):
            raise NativeToolOptionError(f"{key!r} must be a string")
        if len(value) > int(spec.get("max_length", _MAX_STRING_LENGTH)):
            raise NativeToolOptionError(f"{key!r} is too long")
        if tool_name == "image_gen" and key == "size":
            _validate_image_size(value)
        return value
    if kind == "int":
        if value is None:
            return None
        if isinstance(value, bool):
            raise NativeToolOptionError(f"{key!r} must be an integer")
        try:
            coerced_int = int(value)
        except (TypeError, ValueError):
            raise NativeToolOptionError(f"{key!r} must be an integer")
        _validate_number_bounds(key, coerced_int, spec)
        return coerced_int
    if kind == "float":
        if value is None:
            return None
        if isinstance(value, bool):
            raise NativeToolOptionError(f"{key!r} must be a number")
        try:
            coerced_float = float(value)
        except (TypeError, ValueError):
            raise NativeToolOptionError(f"{key!r} must be a number")
        _validate_number_bounds(key, coerced_float, spec)
        return coerced_float
    if kind == "bool":
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        raise NativeToolOptionError(f"{key!r} must be a boolean")
    raise NativeToolOptionError(f"Unsupported option type {kind!r} for {key!r}")


def _validate_number_bounds(key: str, value: int | float, spec: dict[str, Any]) -> None:
    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and value < minimum:
        raise NativeToolOptionError(f"{key!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise NativeToolOptionError(f"{key!r} must be <= {maximum}")


def _validate_image_size(value: str) -> None:
    if value == "auto":
        return
    match = _SIZE_RE.match(value)
    if not match:
        raise NativeToolOptionError("'size' must be 'auto' or WIDTHxHEIGHT")
    width = int(match.group("w"))
    height = int(match.group("h"))
    if not (_MIN_IMAGE_SIDE <= width <= _MAX_IMAGE_SIDE):
        raise NativeToolOptionError(
            f"'size' width must be {_MIN_IMAGE_SIDE}..{_MAX_IMAGE_SIDE}"
        )
    if not (_MIN_IMAGE_SIDE <= height <= _MAX_IMAGE_SIDE):
        raise NativeToolOptionError(
            f"'size' height must be {_MIN_IMAGE_SIDE}..{_MAX_IMAGE_SIDE}"
        )
