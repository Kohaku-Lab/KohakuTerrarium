"""Studio tier — programmatic façade over the studio sub-packages.

The :class:`Studio` facade exposes catalog, identity, session, persistence,
editor, and attachment services over a Terrarium runtime.

Importing the package registers Studio-owned group-tool integrations for session
attachment, creature naming, workspace discovery, and spawnable catalogs. The
Terrarium layer consumes these optional hooks without importing Studio, preserving
the lower layer's independence and graceful behavior when Studio is absent.
"""

from kohakuterrarium.studio.catalog.spawnable import list_spawnable_creatures
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.sessions.find import (
    apply_creature_name as _apply_creature_name,
)
from kohakuterrarium.studio.sessions.lifecycle import (
    attach_session_store_for_creature,
)
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.terrarium import group_hooks as _group_hooks


def _store_attach_hook(engine, creature, *, config_path="", config_type="agent"):
    attach_session_store_for_creature(
        engine, creature, config_path=config_path, config_type=config_type
    )


def _spawnable_hook(workspace):
    return list_spawnable_creatures(workspace=workspace)


def _resolve_workspace_hook(engine, creature):
    pwd = ""
    executor = getattr(creature.agent, "executor", None)
    if executor is not None:
        pwd = str(getattr(executor, "_working_dir", "") or "")
    if not pwd:
        return None
    try:
        return LocalWorkspace.open(pwd)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _wire_group_hooks() -> None:
    """Bind optional Studio behavior to Terrarium's dependency-inversion hooks."""
    _group_hooks.register_store_attach(_store_attach_hook)
    _group_hooks.register_name_apply(_apply_creature_name)
    _group_hooks.register_spawnable(_spawnable_hook)
    _group_hooks.register_workspace_resolver(_resolve_workspace_hook)


_wire_group_hooks()


__all__ = ["Studio"]
