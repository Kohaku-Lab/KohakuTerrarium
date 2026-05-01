"""Validation helpers for plugin option overrides.

Schema shape mirrors :mod:`kohakuterrarium.core.native_tool_validation` so
the two systems use identical option-spec dicts. Plugin authors return
the schema from :meth:`BasePlugin.option_schema` and the validator
coerces user-supplied values against it.

Schema entry::

    {
        "type": "string" | "int" | "float" | "bool" | "enum" | "list" | "dict",
        "default": ...,
        "doc": "Short description",
        # type-specific:
        "values":     [...],         # enum
        "min":        ...,           # int / float
        "max":        ...,           # int / float
        "max_length": ...,           # string
        "item_type":  "string"|...,  # list — element type (no defaults)
        "max_items":  ...,           # list
    }
"""

from typing import Any

_MAX_STRING_LENGTH = 256
_MAX_LIST_ITEMS = 1024


class PluginOptionError(ValueError):
    """Raised when a plugin option override is invalid."""


def validate_plugin_options(
    plugin_name: str,
    values: dict[str, Any],
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate and coerce plugin option overrides.

    Rejects unknown keys and wrong types. Returns a cleaned dict with
    coerced values. Empty schema means the plugin has no declared
    options — any non-empty ``values`` raises.
    """
    if not isinstance(values, dict):
        raise PluginOptionError("values must be an object")
    if not values:
        return {}
    if not schema:
        raise PluginOptionError(
            f"Plugin {plugin_name!r} declares no options; cannot set values"
        )
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key not in schema:
            raise PluginOptionError(
                f"Unknown option {key!r} for plugin {plugin_name!r}"
            )
        spec = schema[key] or {}
        cleaned[key] = _coerce_value(plugin_name, key, value, spec)
    return cleaned


def _coerce_value(plugin_name: str, key: str, value: Any, spec: dict[str, Any]) -> Any:
    kind = str(spec.get("type", "string"))
    if value is None:
        return None
    if kind == "enum":
        allowed = [str(v) for v in (spec.get("values") or [])]
        if not isinstance(value, str):
            raise PluginOptionError(f"{key!r} must be one of {allowed}")
        if value not in allowed:
            raise PluginOptionError(
                f"{key!r} value {value!r} must be one of: {', '.join(allowed)}"
            )
        return value
    if kind == "string":
        if not isinstance(value, str):
            raise PluginOptionError(f"{key!r} must be a string")
        max_len = int(spec.get("max_length", _MAX_STRING_LENGTH))
        if len(value) > max_len:
            raise PluginOptionError(f"{key!r} exceeds max length {max_len}")
        return value
    if kind == "int":
        if isinstance(value, bool):
            raise PluginOptionError(f"{key!r} must be an integer")
        try:
            coerced = int(value)
        except (TypeError, ValueError) as exc:
            raise PluginOptionError(f"{key!r} must be an integer") from exc
        _check_bounds(key, coerced, spec)
        return coerced
    if kind == "float":
        if isinstance(value, bool):
            raise PluginOptionError(f"{key!r} must be a number")
        try:
            coerced_f = float(value)
        except (TypeError, ValueError) as exc:
            raise PluginOptionError(f"{key!r} must be a number") from exc
        _check_bounds(key, coerced_f, spec)
        return coerced_f
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        raise PluginOptionError(f"{key!r} must be a boolean")
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            raise PluginOptionError(f"{key!r} must be a list")
        max_items = int(spec.get("max_items", _MAX_LIST_ITEMS))
        if len(value) > max_items:
            raise PluginOptionError(f"{key!r} exceeds max length {max_items}")
        item_type = spec.get("item_type")
        if item_type is None:
            return list(value)
        item_spec = {"type": item_type}
        return [
            _coerce_value(plugin_name, f"{key}[{i}]", item, item_spec)
            for i, item in enumerate(value)
        ]
    if kind == "dict":
        if not isinstance(value, dict):
            raise PluginOptionError(f"{key!r} must be an object")
        return dict(value)
    raise PluginOptionError(f"Unsupported option type {kind!r} for {key!r}")


def _check_bounds(key: str, value: int | float, spec: dict[str, Any]) -> None:
    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and value < minimum:
        raise PluginOptionError(f"{key!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise PluginOptionError(f"{key!r} must be <= {maximum}")
