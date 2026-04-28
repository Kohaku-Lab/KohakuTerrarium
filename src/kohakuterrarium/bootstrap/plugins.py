"""Bootstrap plugin loading — config + package discovery."""

import importlib
from typing import Any

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
) -> PluginManager:
    """Create a PluginManager with config plugins + discovered packages.

    1. Plugins listed in config are loaded and enabled.
    2. Plugins found in installed packages (but not in config) are
       registered as available but disabled — user can enable at runtime.

    Returns a PluginManager (possibly empty).
    """
    manager = PluginManager()
    config_names: set[str] = set()

    # Phase 1: Load plugins from config (enabled)
    for cfg in plugin_configs or []:
        plugin = _load_one(cfg, loader)
        if plugin:
            config_names.add(plugin.name)
            manager.register(plugin)

    # Phase 2: Discover plugins from installed packages (disabled if not in config)
    _discover_package_plugins(manager, config_names, loader)

    return manager


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

    # If only name given, try to resolve from packages
    if name and not module:
        resolved = _resolve_from_packages(name)
        if resolved:
            module, class_name = resolved
        else:
            logger.debug("Plugin not found in packages", plugin_name=name)
            return None

    if not module or not class_name:
        logger.warning("Plugin missing module/class", plugin_name=name)
        return None

    ptype = cfg.get("type", "package")
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

        if not getattr(plugin, "name", "") or plugin.name == "unnamed":
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
