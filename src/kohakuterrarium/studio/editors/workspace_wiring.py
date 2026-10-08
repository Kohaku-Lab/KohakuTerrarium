"""Workspace-level wiring: a module's config entry, its users, plug / unplug.

``LocalWorkspace`` delegates here; the config edits themselves are the pure
functions of :mod:`.wiring`, applied to round-trip YAML so comments survive.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.studio.catalog.packages_scan import invalidate_scan_caches
from kohakuterrarium.studio.editors import wiring
from kohakuterrarium.studio.editors.utils_paths import sanitize_name
from kohakuterrarium.studio.editors.workspace_manifest import detect_class_name
from kohakuterrarium.studio.editors.yaml_creature import (
    load_creature_file,
    save_creature_file,
)

_IDENTITY_FIELD = {"tools": "tool_name", "plugins": "name", "subagents": "name"}


def _module_file(ws: Any, kind: str, name: str) -> Path:
    path = ws._find_module_file(kind, sanitize_name(name))
    if path is None:
        raise FileNotFoundError(f"{kind}/{name}")
    return path.resolve()


def module_wiring(ws: Any, kind: str, name: str) -> dict:
    """``{ref, name, entry}``: how a creature config loads this module."""
    path = _module_file(ws, kind, name)
    ref = ws.ref_for(path, ws.ref_prefix())
    form = ws.load_module(kind, name).get("form") or {}
    wired_name = form.get(_IDENTITY_FIELD.get(kind, ""), "") or path.stem
    entry = wiring.wiring_entry(
        kind,
        wired_name,
        ref,
        class_name=form.get("class_name") or detect_class_name(path, kind),
        config_var=wiring.detect_config_var(path) if kind == "subagents" else None,
    )
    return {"ref": ref, "name": wired_name, "entry": entry}


def _creature_configs(ws: Any, names: list[str] | None):
    folder = ws.creatures_dir
    if not folder.is_dir():
        return
    wanted = None if names is None else {sanitize_name(n) for n in names}
    for creature_dir in sorted(p for p in folder.iterdir() if p.is_dir()):
        if wanted is not None and creature_dir.name not in wanted:
            continue
        cfg = next(
            (
                creature_dir / f
                for f in ("config.yaml", "config.yml")
                if (creature_dir / f).exists()
            ),
            None,
        )
        if cfg is not None:
            yield creature_dir, cfg


def module_users(ws: Any, kind: str, name: str) -> list[str]:
    """Names of the workspace creatures whose own config loads this module."""
    target = _module_file(ws, kind, name)
    users = []
    for creature_dir, cfg in _creature_configs(ws, None):
        try:
            config = load_creature_file(cfg)
        except Exception:
            continue
        if wiring.uses(config, kind, target, creature_dir):
            users.append(creature_dir.name)
    return users


def module_file_key(ws: Any, kind: str, entry: dict) -> tuple[str, Path]:
    """The ``(kind, resolved file)`` key of a ``list_modules`` entry."""
    return kind, (ws.root_path / entry["path"]).resolve()


def users_by_file(
    ws: Any, listed: dict[str, list[dict]]
) -> dict[tuple[str, Path], list[str]]:
    """For every listed module file, the workspace creatures loading it.

    ``listed`` maps a kind to its ``list_modules`` entries. Each creature
    config is read once, whatever the number of modules.
    """
    files = {
        module_file_key(ws, kind, m)
        for kind, entries in listed.items()
        for m in entries
    }
    out: dict[tuple[str, Path], list[str]] = {key: [] for key in files}
    for creature_dir, cfg in _creature_configs(ws, None):
        try:
            config = load_creature_file(cfg)
        except Exception:
            continue
        for kind, target in files:
            if wiring.uses(config, kind, target, creature_dir):
                out[(kind, target)].append(creature_dir.name)
    return out


def _edit(ws: Any, kind: str, name: str, creatures: list[str], plug: bool) -> list[str]:
    target = _module_file(ws, kind, name)
    info = module_wiring(ws, kind, name) if plug else None
    found = {d.name: (d, cfg) for d, cfg in _creature_configs(ws, creatures)}
    missing = [c for c in creatures if sanitize_name(c) not in found]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    changed = []
    for creature, (creature_dir, cfg) in found.items():
        config = load_creature_file(cfg)
        if plug:
            did = wiring.plug(
                config, kind, info["name"], info["entry"], target, creature_dir
            )
        else:
            did = wiring.unplug(config, kind, target, creature_dir)
        if did:
            save_creature_file(cfg, config)
            changed.append(creature)
    if changed:
        invalidate_scan_caches()
    return changed


def plug_module(ws: Any, kind: str, name: str, creatures: list[str]) -> list[str]:
    """Wire the module into each named creature; returns those that changed."""
    return _edit(ws, kind, name, creatures, plug=True)


def unplug_module(ws: Any, kind: str, name: str, creatures: list[str]) -> list[str]:
    """Remove the module from each named creature; returns those that changed."""
    return _edit(ws, kind, name, creatures, plug=False)
