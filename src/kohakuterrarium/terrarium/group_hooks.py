"""Studio-supplied hooks for the group tool surface.

The ``terrarium`` layer must not import from ``studio`` (layer rule
enforced by :mod:`tests/unit/test_layer_independence`). Studio-tier
behaviour the group tools want to invoke — session-store auto-attach,
display-name propagation, the spawnable creature catalog — is plugged
in here at import time from the studio side.

When no hook is registered, calls degrade gracefully:

- :func:`attach_session_store` becomes a no-op (the spawned creature
  still runs, just without persistence).
- :func:`apply_creature_name` falls back to ``creature.name = name``.
- :func:`list_spawnable` returns an empty list (the model's
  ``group_status`` simply omits the catalog).
- :func:`resolve_workspace` returns ``None`` (workspace-scoped lookups
  fall back to package-only).

This keeps ``terrarium`` runnable in tests and headless usage without
the studio layer present.
"""

from typing import Any, Callable

# (engine, creature, *, config_path: str = "", config_type: str = "agent") -> None
StoreAttachHook = Callable[..., None]
# (creature, name) -> None
NameApplyHook = Callable[[Any, str], None]
# (workspace) -> list[dict]
SpawnableHook = Callable[[Any], list[dict]]
# (engine, creature) -> Any (workspace handle or None)
WorkspaceResolverHook = Callable[..., Any]


_store_attach: StoreAttachHook | None = None
_name_apply: NameApplyHook | None = None
_spawnable: SpawnableHook | None = None
_workspace_resolver: WorkspaceResolverHook | None = None


def register_store_attach(hook: StoreAttachHook) -> None:
    global _store_attach
    _store_attach = hook


def register_name_apply(hook: NameApplyHook) -> None:
    global _name_apply
    _name_apply = hook


def register_spawnable(hook: SpawnableHook) -> None:
    global _spawnable
    _spawnable = hook


def register_workspace_resolver(hook: WorkspaceResolverHook) -> None:
    global _workspace_resolver
    _workspace_resolver = hook


def attach_session_store(
    engine: Any,
    creature: Any,
    *,
    config_path: str = "",
    config_type: str = "agent",
) -> None:
    if _store_attach is None:
        return
    try:
        _store_attach(
            engine, creature, config_path=config_path, config_type=config_type
        )
    except Exception:
        pass


def apply_creature_name(creature: Any, name: str) -> None:
    if _name_apply is None:
        creature.name = name
        return
    try:
        _name_apply(creature, name)
    except Exception:
        creature.name = name


def list_spawnable(workspace: Any | None) -> list[dict]:
    if _spawnable is None:
        return []
    try:
        return _spawnable(workspace)
    except Exception:
        return []


def resolve_workspace(engine: Any, creature: Any) -> Any | None:
    if _workspace_resolver is None:
        return None
    try:
        return _workspace_resolver(engine, creature)
    except Exception:
        return None
