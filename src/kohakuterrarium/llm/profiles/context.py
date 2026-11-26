"""Context-size selections and the legacy ``context`` variation group.

``context=<n>`` sets ``max_context`` on any preset. Older presets spell the
same thing as a ``context`` group whose options only set ``max_context``;
those groups are folded into the numeric selection here.
"""

import re
from decimal import Decimal
from typing import Any

# Selection key that sets ``max_context`` on any preset, with or without a group.
CONTEXT_SELECTION_KEY = "context"

_CONTEXT_SIZE = re.compile(r"(\d+(?:\.\d+)?|\.\d+)([km]?)")
_CONTEXT_UNITS = {"": 1, "k": 1000, "m": 1_000_000}


def parse_context_size(value: str) -> int:
    """Token count for a context selection such as ``500000``, ``272k`` or ``1.5m``."""
    text = str(value).strip().lower()
    match = _CONTEXT_SIZE.fullmatch(text)
    tokens = Decimal(match[1]) * _CONTEXT_UNITS[match[2]] if match else Decimal(0)
    if tokens <= 0 or tokens != tokens.to_integral_value():
        raise ValueError(
            f"Invalid context size '{value}': use a positive whole number of "
            "tokens, or <n>k / <n>m (for example 272k or 1m)"
        )
    return int(tokens)


def format_context_size(tokens: int) -> str:
    """Canonical selector text for a token count; reparses to the same number."""
    if tokens % 1_000_000 == 0:
        return f"{tokens // 1_000_000}m"
    if tokens % 1000 == 0:
        return f"{tokens // 1000}k"
    return str(tokens)


def context_size_or_none(value: str) -> int | None:
    """``parse_context_size`` that answers ``None`` instead of raising."""
    try:
        return parse_context_size(value)
    except ValueError:
        return None


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def context_aliases(value: Any) -> dict[str, int]:
    """The well-formed ``name -> tokens`` entries of a stored alias map."""
    if not isinstance(value, dict):
        return {}
    return {
        name: tokens
        for name, tokens in value.items()
        if isinstance(name, str) and name and _positive_int(tokens)
    }


def legacy_context_values(options: Any) -> dict[str, int] | None:
    """Option -> tokens when every option of a group sets exactly ``max_context``."""
    if not isinstance(options, dict) or not options:
        return None
    values: dict[str, int] = {}
    for name, patch in options.items():
        if not isinstance(name, str) or not isinstance(patch, dict):
            return None
        if set(patch) != {"max_context"} or not _positive_int(patch["max_context"]):
            return None
        values[name] = patch["max_context"]
    return values


def fold_legacy_context(
    groups: Any, window: int | None, aliases: dict[str, int]
) -> tuple[dict[str, Any], int | None, dict[str, int]] | None:
    """Groups, window and aliases with a legacy ``context`` group folded out.

    The window defaults to the largest option. An option becomes an alias
    when its name is not a size, or is a size naming a different number.
    ``None`` when ``groups`` has no legacy ``context`` group.
    """
    if not isinstance(groups, dict):
        return None
    values = legacy_context_values(groups.get(CONTEXT_SELECTION_KEY))
    if values is None:
        return None
    rest = {
        name: opts for name, opts in groups.items() if name != CONTEXT_SELECTION_KEY
    }
    named = {
        name: tokens
        for name, tokens in values.items()
        if context_size_or_none(name) != tokens
    }
    if window is None:
        window = max(values.values())
    return rest, window, {**aliases, **named}


def fold_preset_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """A preset field map with its legacy ``context`` group folded out."""
    window = fields.get("context_window")
    folded = fold_legacy_context(
        fields.get("variation_groups"),
        window if _positive_int(window) else None,
        context_aliases(fields.get("context_aliases")),
    )
    if folded is None:
        return fields
    groups, window, aliases = folded
    result = {
        key: value
        for key, value in fields.items()
        if key not in ("variation_groups", "context_aliases")
    }
    if groups:
        result["variation_groups"] = groups
    result["context_window"] = window
    if aliases:
        result["context_aliases"] = aliases
    return result
