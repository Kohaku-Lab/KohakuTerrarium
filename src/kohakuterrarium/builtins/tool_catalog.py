"""
Builtin tool catalog: pure lookup and registration.

This is a leaf module with zero side effects. It holds the global
registry dict and provides lookup/factory functions. Individual tool
modules use ``@register_builtin`` to register themselves at import
time, but this module never imports any tool module itself.

Supports **deferred loaders**: callables registered via
``register_deferred_loader`` that are invoked on first cache miss.
This lets the terrarium layer register its tools lazily without
the catalog knowing anything about terrarium internals.

Internal code (core, terrarium) should import from here, not from
``builtins.tools``, to avoid pulling in all tool modules and their
transitive dependencies.
"""

from typing import TYPE_CHECKING, Callable, TypeVar

from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig

logger = get_logger(__name__)

# Global registry of built-in tool classes, populated by @register_builtin
_BUILTIN_TOOLS: dict[str, type["BaseTool"]] = {}

# Deferred loaders: callables that, when invoked, import additional tool
# modules and trigger their @register_builtin decorators. Each loader is
# called at most once (on first catalog miss), then removed.
_DEFERRED_LOADERS: list[Callable[[], None]] = []

T = TypeVar("T", bound="BaseTool")


def register_builtin(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator to register a built-in tool class.

    Usage::

        @register_builtin("bash")
        class BashTool(BaseTool):
            ...
    """

    def decorator(cls: type[T]) -> type[T]:
        _BUILTIN_TOOLS[name] = cls
        logger.debug("Registered builtin tool", tool_name=name)
        return cls

    return decorator


def register_deferred_loader(loader: Callable[[], None]) -> None:
    """Register a callable that loads additional tool modules on demand.

    The loader is invoked the first time ``get_builtin_tool`` encounters
    a name not yet in the catalog. After all deferred loaders have fired,
    they are cleared so they never run again.

    Example::

        register_deferred_loader(ensure_terrarium_tools_registered)
    """
    _DEFERRED_LOADERS.append(loader)


def _run_deferred_loaders() -> None:
    """Invoke and clear all deferred loaders."""
    if not _DEFERRED_LOADERS:
        return
    # Copy + clear before calling to avoid re-entrance
    loaders = list(_DEFERRED_LOADERS)
    _DEFERRED_LOADERS.clear()
    for loader in loaders:
        loader()


def get_builtin_tool(
    name: str, config: "ToolConfig | None" = None
) -> "BaseTool | None":
    """Get an instance of a built-in tool by name.

    On first miss, invokes any registered deferred loaders (which may
    populate the catalog with additional tools) and retries.
    Returns None if still not found after all loaders have run.
    """
    tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls is None and _DEFERRED_LOADERS:
        _run_deferred_loaders()
        tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls:
        return tool_cls(config=config)
    return None


def list_builtin_tools() -> list[str]:
    """List all registered built-in tool names."""
    return list(_BUILTIN_TOOLS.keys())


def is_builtin_tool(name: str) -> bool:
    """Check if a tool name is a registered built-in."""
    return name in _BUILTIN_TOOLS
