"""Built-in plugin catalog and default-pack expansion."""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_PLUGINS: dict[str, dict[str, str]] = {
    "budget.ticker": {
        "module": "kohakuterrarium.builtins.plugins.budget.ticker",
        "class": "BudgetTickerPlugin",
    },
    "budget.alarm": {
        "module": "kohakuterrarium.builtins.plugins.budget.alarm",
        "class": "BudgetAlarmPlugin",
    },
    "budget.gate": {
        "module": "kohakuterrarium.builtins.plugins.budget.gate",
        "class": "BudgetGatePlugin",
    },
    "compact.auto": {
        "module": "kohakuterrarium.builtins.plugins.compact.auto",
        "class": "AutoCompactPlugin",
    },
}

_PACKS: dict[str, list[str]] = {
    "budget": ["budget.ticker", "budget.alarm", "budget.gate"],
    "auto-compact": ["compact.auto"],
    "default-runtime": [
        "budget.ticker",
        "budget.alarm",
        "budget.gate",
        "compact.auto",
    ],
}


def resolve_plugin_specs(names: list[str]) -> list[dict[str, Any]]:
    """Expand built-in plugin pack names and aliases into plugin specs."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in names or []:
        for resolved in _PACKS.get(name, [name]):
            if resolved in seen:
                continue
            spec = _PLUGINS.get(resolved)
            if spec is None:
                logger.warning("unknown_default_plugin", plugin_name=resolved)
                continue
            seen.add(resolved)
            out.append({"name": resolved, "type": "package", **spec})
    return out
