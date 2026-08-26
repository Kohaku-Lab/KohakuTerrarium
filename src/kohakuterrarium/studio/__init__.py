"""Studio management facade and optional Terrarium integrations."""

import importlib

from kohakuterrarium.studio import hooks as _hooks

_name_apply_hook = _hooks.name_apply_hook
_resolve_workspace_hook = _hooks.resolve_workspace_hook
_spawnable_hook = _hooks.spawnable_hook
_store_attach_hook = _hooks.store_attach_hook
_wire_group_hooks = _hooks.register_group_hooks

_wire_group_hooks()

_EXPORTS = {"Studio": "kohakuterrarium.studio.studio"}

__all__ = ["Studio"]


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
