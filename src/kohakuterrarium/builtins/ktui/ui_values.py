"""Prop values of kt.ui spec nodes, evaluated in a block's scope (shared by the ui_* modules)."""

from typing import Any

from kohakuterrarium.builtins.kt_ui.expr import ExprError, interpolate
from kohakuterrarium.builtins.kt_ui.values import to_display


def value(raw: Any, scope: dict[str, Any]) -> Any:
    """A prop with its ``{{ expr }}`` templates evaluated; a failing expression gives "?"."""
    if not isinstance(raw, str):
        return raw
    try:
        return interpolate(raw, scope)
    except ExprError:
        return "?"


def text(raw: Any, scope: dict[str, Any]) -> str:
    """A prop as display text ("" when unset)."""
    return to_display(value(raw, scope)) if raw is not None else ""


def items(raw: Any, scope: dict[str, Any]) -> list:
    """A list prop ([] when it is not one)."""
    got = value(raw, scope)
    return got if isinstance(got, list) else []


def number(raw: Any, scope: dict[str, Any]) -> float | None:
    """A numeric prop (None when it is not a number)."""
    got = value(raw, scope)
    return got if isinstance(got, (int, float)) and not isinstance(got, bool) else None


__all__ = ["items", "number", "text", "value"]
