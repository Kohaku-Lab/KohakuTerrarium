"""Bootstrap plugin loading — config + package discovery."""

import importlib
from typing import Any

from kohakuterrarium.errors import ConfigError
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
    *,
    strict: bool = False,
) -> PluginManager:
    """Load configured plugins and register discovered plugins as disabled.

    Strict mode rejects configured plugin failures because silently dropping a
    policy plugin is unsafe. Discovery remains lenient so unrelated broken
    packages cannot block the agent.
    """
    manager = PluginManager()
    merged_configs = _merge_default_plugin_specs(
        plugin_configs or [], default_plugins or [], default_plugin_specs or []
    )
    config_names: set[str] = set()

    # Make package modules importable before loading configured package plugins.
    _pre_import_packages_for_config(merged_configs)

    # Configured plugins are active.
    for cfg in merged_configs:
        plugin = _load_one(cfg, loader, strict=strict)
        if plugin:
            config_names.add(plugin.name)
            manager.register(plugin)

    # Unconfigured catalog plugins remain available for runtime enablement.
    _discover_catalog_plugins(manager, config_names, loader)

    # Unconfigured package plugins also remain available but inactive.
    _discover_package_plugins(manager, config_names, loader)

    return manager


def _pre_import_packages_for_config(plugin_configs: list[dict[str, Any]]) -> None:
    """Expose installed package modules before loading configured plugins.

    Config entries identify Python modules rather than package names, so every
    installed package must be made importable when any package plugin is used.
    """
    if not plugin_configs:
        return
    has_package_plugin = any(
        isinstance(cfg, dict) and cfg.get("type", "package") == "package"
        for cfg in plugin_configs
    )
    if not has_package_plugin:
        return
    try:
        for pkg in list_packages():
            name = pkg.get("name", "")
            if name:
                ensure_package_importable(name)
    except Exception as e:
        logger.warning(
            "Pre-import package walk failed; falling back to Phase-2 discovery",
            error=str(e),
            exc_info=True,
        )


def _discover_catalog_plugins(
    manager: PluginManager, already_loaded: set[str], loader: ModuleLoader | None
) -> None:
    """Register unconfigured built-in plugins as disabled but available."""
    for spec in list_catalog_plugins():
        name = spec.get("name", "")
        if not name or name in already_loaded:
            continue
        plugin = _load_one(spec, loader)
        if plugin:
            manager.register(plugin)
            manager.disable(name)  # Discovery must not activate policy implicitly.


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
    cfg: dict[str, Any] | str,
    loader: ModuleLoader | None,
    *,
    strict: bool = False,
) -> BasePlugin | None:
    """Load one plugin, raising configured failures only in strict mode."""
    if isinstance(cfg, str):
        cfg = {"name": cfg}

    name = cfg.get("name", "")
    module = cfg.get("module", "")
    class_name = cfg.get("class", cfg.get("class_name", ""))
    options = cfg.get("options", {})

    # Name-only entries prefer the built-in catalog before installed packages.
    if name and not module:
        resolved = _resolve_from_catalog(name) or _resolve_from_packages(name)
        if resolved:
            module, class_name = resolved
        else:
            if strict:
                raise ConfigError(
                    f"Plugin {name!r} not found in the builtin catalog "
                    "or any installed package"
                )
            logger.debug("Plugin not found", plugin_name=name)
            return None

    if not module or not class_name:
        if strict:
            raise ConfigError(f"Plugin {name!r} is missing module/class")
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
            # Both load paths pass options as constructor keyword arguments.
            mod = importlib.import_module(module)
            cls = getattr(mod, class_name)
            plugin = cls(**options) if options else cls()

        if not isinstance(plugin, BasePlugin):
            if strict:
                raise ConfigError(
                    f"Plugin {name!r} ({module}.{class_name}) is not a " "BasePlugin"
                )
            logger.warning("Not a BasePlugin", plugin_name=name)
            return None

        if name:
            plugin.name = name
        elif not getattr(plugin, "name", "") or plugin.name == "unnamed":
            plugin.name = name
        if not getattr(plugin, "description", "") and cfg.get("description"):
            plugin.description = cfg["description"]
        return plugin

    except ConfigError:
        raise
    except Exception as e:
        if strict:
            raise ConfigError(
                f"Failed to load plugin {name!r} " f"({module}.{class_name}): {e}"
            ) from e
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
        logger.warning(
            "Failed to list packages for plugin discovery", error=str(e), exc_info=True
        )
        return

    for pkg in packages:
        if not pkg.get("plugins"):
            continue
        # Package discovery may target modules outside the active environment.
        ensure_package_importable(pkg["name"])
        for plugin_def in pkg.get("plugins", []):
            if not isinstance(plugin_def, dict):
                continue
            name = plugin_def.get("name", "")
            if not name or name in already_loaded:
                continue
            plugin = _load_one(plugin_def, loader)
            if plugin:
                manager.register(plugin)
                manager.disable(name)  # Discovery must not activate policy implicitly.


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
        logger.warning(
            "Failed to resolve plugin from packages", error=str(e), exc_info=True
        )
    return None
