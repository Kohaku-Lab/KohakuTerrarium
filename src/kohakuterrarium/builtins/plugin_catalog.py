"""Built-in plugin catalog and default-pack expansion."""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_PLUGINS: dict[str, dict[str, str]] = {
    "budget": {
        "module": "kohakuterrarium.builtins.plugins.budget.plugin",
        "class": "BudgetPlugin",
    },
    "compact.auto": {
        "module": "kohakuterrarium.builtins.plugins.compact.auto",
        "class": "AutoCompactPlugin",
    },
    "permgate": {
        "module": "kohakuterrarium.builtins.plugins.permgate.plugin",
        "class": "PermGatePlugin",
    },
    "sandbox": {
        "module": "kohakuterrarium.builtins.plugins.sandbox.plugin",
        "class": "SandboxPlugin",
    },
}

# Plugin packs are syntactic sugar for opting into multiple plugins by
# name. Budget is intentionally NOT bundled into any default pack — it
# is opt-in per agent / sub-agent and must be requested explicitly with
# its own ``options`` so its axes are visible at the call site.
_PACKS: dict[str, list[str]] = {
    "auto-compact": ["compact.auto"],
}


def lookup_plugin(name: str) -> dict[str, str] | None:
    """Return the catalog spec for a built-in plugin name, or ``None``."""
    spec = _PLUGINS.get(name)
    return dict(spec) if spec else None


def list_catalog_plugins() -> list[dict[str, Any]]:
    """Return all built-in plugins from the catalog as load-spec dicts.

    Used by ``bootstrap.plugins`` to register catalog-listed plugins
    as disabled-but-available so they appear in the frontend's plugin
    list (Plugins tab → Enable button) without requiring an explicit
    entry in the agent's YAML config.
    """
    return [
        {"name": name, "type": "package", **dict(spec)}
        for name, spec in _PLUGINS.items()
    ]


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
