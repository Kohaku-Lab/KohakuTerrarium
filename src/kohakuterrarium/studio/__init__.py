"""Studio tier — programmatic façade over the studio sub-packages.

The :class:`Studio` class wraps a :class:`Terrarium` engine and
exposes catalog / identity / sessions / persistence / editors /
attach as nested namespaces.  See
``plans/structure-hierarchy/studio-class/design.md``.

Importing this package also registers the studio-supplied hooks the
``terrarium`` group tools call into (session-store auto-attach,
creature-name propagation, spawnable creature catalog). The terrarium
layer never imports ``studio`` directly — it consumes whatever has
been registered, and degrades gracefully when nothing has.
"""

from kohakuterrarium.studio.catalog.spawnable import list_spawnable_creatures
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.sessions.lifecycle import (
    _apply_creature_name,
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
    """Register studio-side implementations of the group_hooks contract."""
    _group_hooks.register_store_attach(_store_attach_hook)
    _group_hooks.register_name_apply(_apply_creature_name)
    _group_hooks.register_spawnable(_spawnable_hook)
    _group_hooks.register_workspace_resolver(_resolve_workspace_hook)


_wire_group_hooks()


__all__ = ["Studio"]
