"""Bootstrap plugin loading — config + package discovery."""

import importlib
from typing import Any

from kohakuterrarium.builtins.plugin_catalog import (
    list_catalog_plugins,
    lookup_plugin,
    resolve_plugin_specs,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.packages.resolve import ensure_package_importable
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def init_plugins(
    plugin_configs: list[dict[str, Any]],
    loader: ModuleLoader | None = None,
    default_plugins: list[str] | None = None,
    default_plugin_specs: list[dict[str, Any]] | None = None,
) -> PluginManager:
    """Create a PluginManager with config plugins + discovered packages.

    1. Plugins listed in config are loaded and enabled.
    2. Plugins found in installed packages (but not in config) are
       registered as available but disabled — user can enable at runtime.

    Returns a PluginManager (possibly empty).
    """
    manager = PluginManager()
    merged_configs = _merge_default_plugin_specs(
        plugin_configs or [], default_plugins or [], default_plugin_specs or []
    )
    config_names: set[str] = set()

    # Phase 1: Load plugins from config (enabled)
    for cfg in merged_configs:
        plugin = _load_one(cfg, loader)
        if plugin:
            config_names.add(plugin.name)
            manager.register(plugin)

    # Phase 1.5: Discover built-in catalog plugins not in config
    # (registered as disabled — visible in the frontend Plugins tab
    # with an "Enable" button).
    _discover_catalog_plugins(manager, config_names, loader)

    # Phase 2: Discover plugins from installed packages (disabled if not in config)
    _discover_package_plugins(manager, config_names, loader)

    return manager


def _discover_catalog_plugins(
    manager: PluginManager, already_loaded: set[str], loader: ModuleLoader | None
) -> None:
    """Register built-in catalog plugins (e.g. permgate, budget) as
    disabled-but-available when they're not already loaded via config.

    This is the third tier of plugin discovery alongside config (Phase
    1) and package discovery (Phase 2): the frontend's plugin list
    shows everything in the agent's plugin manager, so registering
    catalog entries here is what makes them opt-in via UI.
    """
    for spec in list_catalog_plugins():
        name = spec.get("name", "")
        if not name or name in already_loaded:
            continue
        plugin = _load_one(spec, loader)
        if plugin:
            manager.register(plugin)
            manager.disable(name)  # available but not active


def _merge_default_plugin_specs(
    plugin_configs: list[dict[str, Any]],
    default_plugins: list[str],
    default_plugin_specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    defaults = list(default_plugin_specs) + resolve_plugin_specs(default_plugins)
    explicit_names = {
        cfg.get("name")
        for cfg in plugin_configs
        if isinstance(cfg, dict) and cfg.get("name")
    }
    merged = list(plugin_configs)
    for spec in defaults:
        if spec.get("name") in explicit_names:
            continue
        merged.append(spec)
    return merged


def _load_one(
    cfg: dict[str, Any] | str, loader: ModuleLoader | None
) -> BasePlugin | None:
    """Load a single plugin from a config entry."""
    if isinstance(cfg, str):
        cfg = {"name": cfg}

    name = cfg.get("name", "")
    module = cfg.get("module", "")
    class_name = cfg.get("class", cfg.get("class_name", ""))
    options = cfg.get("options", {})

    # If only name given, resolve from the built-in catalog first, then
    # fall back to plugins shipped by installed packages.
    if name and not module:
        resolved = _resolve_from_catalog(name) or _resolve_from_packages(name)
        if resolved:
            module, class_name = resolved
        else:
            logger.debug("Plugin not found", plugin_name=name)
            return None

    if not module or not class_name:
        logger.warning("Plugin missing module/class", plugin_name=name)
        return None

    ptype = cfg.get("type", "package")
    if module.startswith("kohakuterrarium."):
        ptype = "package"
    try:
        if loader:
            plugin = loader.load_instance(
                module, class_name, module_type=ptype, options=options
            )
        else:
            mod = importlib.import_module(module)
            cls = getattr(mod, class_name)
            plugin = cls(options=options) if options else cls()

        if not isinstance(plugin, BasePlugin):
            logger.warning("Not a BasePlugin", plugin_name=name)
            return None

        if name:
            plugin.name = name
        elif not getattr(plugin, "name", "") or plugin.name == "unnamed":
            plugin.name = name
        if not getattr(plugin, "description", "") and cfg.get("description"):
            plugin.description = cfg["description"]
        return plugin

    except Exception as e:
        logger.warning(
            "Failed to load plugin", plugin_name=name, error=str(e), exc_info=True
        )
        return None


def _discover_package_plugins(
    manager: PluginManager, already_loaded: set[str], loader: ModuleLoader | None
) -> None:
    """Scan installed packages and register undiscovered plugins as disabled."""
    try:
        packages = list_packages()
    except Exception as e:
        logger.debug(
            "Failed to list packages for plugin discovery", error=str(e), exc_info=True
        )
        return

    for pkg in packages:
        if not pkg.get("plugins"):
            continue
        # Make the package's Python modules importable
        ensure_package_importable(pkg["name"])
        for plugin_def in pkg.get("plugins", []):
            if not isinstance(plugin_def, dict):
                continue
            name = plugin_def.get("name", "")
            if not name or name in already_loaded:
                continue
            # Try to load it
            plugin = _load_one(plugin_def, loader)
            if plugin:
                manager.register(plugin)
                manager.disable(name)  # Available but not active


def _resolve_from_catalog(name: str) -> tuple[str, str] | None:
    """Find a built-in plugin by name in the catalog."""
    spec = lookup_plugin(name)
    if spec is None:
        return None
    module = spec.get("module", "")
    cls = spec.get("class") or spec.get("class_name", "")
    if module and cls:
        return (module, cls)
    return None


def _resolve_from_packages(name: str) -> tuple[str, str] | None:
    """Find a plugin by name in installed packages."""
    try:
        for pkg in list_packages():
            for pdef in pkg.get("plugins", []):
                if isinstance(pdef, dict) and pdef.get("name") == name:
                    ensure_package_importable(pkg["name"])
                    module = pdef.get("module", "")
                    cls = pdef.get("class") or pdef.get("class_name", "")
                    if module and cls:
                        return (module, cls)
    except Exception as e:
        logger.debug(
            "Failed to resolve plugin from packages", error=str(e), exc_info=True
        )
    return None
