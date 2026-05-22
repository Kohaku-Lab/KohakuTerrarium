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
from kohakuterrarium.utils.mobile_sandbox import is_mobile_profile

if TYPE_CHECKING:
    from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig

logger = get_logger(__name__)


# Tools intentionally HIDDEN under ``KT_PROFILE=mobile``.  These
# tools have no working implementation on Android even with the
# bundled busybox sandbox — either because the platform doesn't
# provide the substrate (long-lived subprocess workers, kernel
# features), or because the Android SAF model breaks the tool's
# contract (arbitrary path access).
#
# The list is opt-out (not opt-in) so brand-new tools default to
# being available everywhere; only platform-incompatible tools
# need to be marked.  ``bash`` is NOT here because we ship a
# bundled busybox sandbox that makes shell-out workable on Android.
_MOBILE_HIDDEN_TOOLS: frozenset[str] = frozenset({})


def _is_hidden_under_mobile_profile(name: str) -> bool:
    """Cheap predicate used by the lookup / list helpers below.

    Lives at module scope (no inner imports) so the catalog stays
    side-effect-free; the env-var read inside ``is_mobile_profile``
    is the only runtime cost.
    """
    return is_mobile_profile() and name in _MOBILE_HIDDEN_TOOLS


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

    Under ``KT_PROFILE=mobile``, tools listed in
    :data:`_MOBILE_HIDDEN_TOOLS` are unreachable — callers see
    ``None`` exactly as they would for a missing tool, so the LLM
    can't accidentally invoke a platform-broken tool by name.
    """
    if _is_hidden_under_mobile_profile(name):
        return None
    tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls is None and _DEFERRED_LOADERS:
        _run_deferred_loaders()
        tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls:
        return tool_cls(config=config)
    return None


def list_builtin_tools() -> list[str]:
    """List all registered built-in tool names.

    Under ``KT_PROFILE=mobile`` the listing is filtered through
    :data:`_MOBILE_HIDDEN_TOOLS` so callers (the system-prompt
    aggregator, `kt config tools list`, the frontend's Tools panel)
    see only the catalog that actually works on the device.
    """
    if is_mobile_profile() and _MOBILE_HIDDEN_TOOLS:
        return [n for n in _BUILTIN_TOOLS if n not in _MOBILE_HIDDEN_TOOLS]
    return list(_BUILTIN_TOOLS.keys())


def is_builtin_tool(name: str) -> bool:
    """Check if a tool name is a registered built-in.

    Honours ``KT_PROFILE=mobile`` — hidden tools report ``False``
    so existence checks line up with the lookup contract.
    """
    if _is_hidden_under_mobile_profile(name):
        return False
    return name in _BUILTIN_TOOLS


def list_provider_native_tools() -> list[dict[str, object]]:
    """Return metadata for every registered provider-native tool.

    Used by ``kt config``, the rich CLI, and the frontend settings page
    to render the "which native tools does this backend expose?"
    checkbox list. Each entry carries the canonical tool name, the
    declared ``provider_support`` set, and a one-line description so
    the UI can show "image_gen — Codex-only, generate/edit images".

    Fires deferred loaders first so terrarium-registered tools (and any
    other lazy additions) show up too.
    """
    if _DEFERRED_LOADERS:
        _run_deferred_loaders()
    out: list[dict[str, object]] = []
    for name, tool_cls in _BUILTIN_TOOLS.items():
        if not getattr(tool_cls, "is_provider_native", False):
            continue
        support = getattr(tool_cls, "provider_support", frozenset()) or frozenset()
        try:
            instance = tool_cls()
            description = getattr(instance, "description", "") or ""
        except Exception:
            description = ""
        schema_fn = getattr(tool_cls, "provider_native_option_schema", None)
        try:
            option_schema = schema_fn() if callable(schema_fn) else {}
        except Exception:
            option_schema = {}
        out.append(
            {
                "name": name,
                "provider_support": sorted(support),
                "description": description,
                "option_schema": option_schema,
            }
        )
    out.sort(key=lambda entry: entry["name"])
    return out


def get_provider_native_option_schema(name: str) -> dict[str, dict[str, object]]:
    """Return the option schema for a single provider-native tool.

    Returns an empty dict if the tool is unknown or not provider-native.
    Used by the bootstrap merge step and the slash-command/CLI/TUI
    flows when they need to validate or render a single tool's form.
    """
    tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls is None and _DEFERRED_LOADERS:
        _run_deferred_loaders()
        tool_cls = _BUILTIN_TOOLS.get(name)
    if tool_cls is None or not getattr(tool_cls, "is_provider_native", False):
        return {}
    schema_fn = getattr(tool_cls, "provider_native_option_schema", None)
    if not callable(schema_fn):
        return {}
    try:
        return schema_fn() or {}
    except Exception:
        return {}
