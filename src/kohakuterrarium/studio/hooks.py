"""Lightweight Studio integrations registered with the Terrarium layer."""

import importlib


def store_attach_hook(engine, creature, *, config_path="", config_type="agent"):
    from kohakuterrarium.studio.sessions.lifecycle import (
        attach_session_store_for_creature,
    )

    attach_session_store_for_creature(
        engine, creature, config_path=config_path, config_type=config_type
    )


def name_apply_hook(creature, name: str) -> None:
    from kohakuterrarium.terrarium.creature_host import apply_creature_name

    apply_creature_name(creature, name)


def spawnable_hook(workspace):
    from kohakuterrarium.studio.catalog.spawnable import list_spawnable_creatures

    return list_spawnable_creatures(workspace=workspace)


def resolve_workspace_hook(engine, creature):
    from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

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


def register_group_hooks() -> None:
    group_hooks = importlib.import_module("kohakuterrarium.terrarium.group_hooks")
    group_hooks.register_store_attach(store_attach_hook)
    group_hooks.register_name_apply(name_apply_hook)
    group_hooks.register_spawnable(spawnable_hook)
    group_hooks.register_workspace_resolver(resolve_workspace_hook)
