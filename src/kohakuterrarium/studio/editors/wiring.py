"""Plug a module file into creature configs, unplug it, and find who uses it.

A module is wired as ``type: custom`` with ``module:`` its reference
(``@/modules/tools/x.py``, ``@pkg/...`` or an absolute path). Tools, sub-agents,
triggers and plugins join their config list; an input replaces ``input:``; an
output joins ``output.named_outputs`` under its name. Entries are matched by
the file they load, whatever spelling (ref, relative or absolute path, the
dotted import path, or a manifest name on a name-only entry) they use.
"""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from kohakuterrarium.packages.resolve import is_package_ref, resolve_package_path

LIST_KEYS = {
    "tools": "tools",
    "subagents": "subagents",
    "triggers": "triggers",
    "plugins": "plugins",
}


def wiring_entry(
    kind: str,
    name: str,
    ref: str,
    *,
    class_name: str | None = None,
    config_var: str | None = None,
) -> dict:
    """The config entry that loads a ``kind`` module from ``ref``."""
    entry: dict[str, Any] = {"type": "custom", "module": ref}
    if kind in LIST_KEYS:
        entry = {"name": name, **entry}
    if kind == "subagents":
        if config_var:
            entry["config"] = config_var
    elif class_name:
        entry["class"] = class_name
    return entry


def detect_config_var(py_path: Path) -> str | None:
    """The first module-level name bound to a ``SubAgentConfig(...)`` call."""
    try:
        return config_var_of(py_path.read_text(encoding="utf-8"))
    except OSError:
        return None


def first_class_name(source: str) -> str | None:
    """The first module-level class defined in ``source``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return next((n.name for n in tree.body if isinstance(n, ast.ClassDef)), None)


def config_var_of(source: str) -> str | None:
    """The first module-level name of ``source`` bound to ``SubAgentConfig(...)``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        called = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        )
        if called == "SubAgentConfig" and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
    return None


def _module_path(value: Any, creature_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if is_package_ref(value):
            return resolve_package_path(value)
        path = Path(value).expanduser()
        return (path if path.is_absolute() else creature_dir / path).resolve()
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class Target:
    """A module file and the other spellings a config may load it by.

    ``dotted`` is its import path (``kt_biome.tools.database``), loaded as
    ``type: package``; ``names`` are the manifest names a name-only entry
    resolves to it by.
    """

    path: Path
    dotted: str = ""
    names: frozenset[str] = frozenset()


def _loads(entry: Any, target: Target | Path, creature_dir: Path) -> bool:
    if isinstance(target, Path):
        target = Target(target)
    if not isinstance(entry, dict):
        return False
    module = entry.get("module")
    if not module:
        builtin = entry.get("type") in ("builtin", "trigger")
        return not builtin and entry.get("name") in target.names
    if target.dotted and module == target.dotted:
        return True
    return (
        entry.get("type", "custom") == "custom"
        and _module_path(module, creature_dir) == target.path
    )


def uses(config: dict, kind: str, target: Target | Path, creature_dir: Path) -> bool:
    """Whether ``config`` (of the creature in ``creature_dir``) loads ``target``."""
    if kind in LIST_KEYS:
        items = config.get(LIST_KEYS[kind]) or []
        return any(_loads(e, target, creature_dir) for e in items)
    if kind == "inputs":
        return _loads(config.get("input"), target, creature_dir)
    output = config.get("output") or {}
    named = (output.get("named_outputs") or {}).values()
    return _loads(output, target, creature_dir) or any(
        _loads(e, target, creature_dir) for e in named
    )


def plug(
    config: CommentedMap,
    kind: str,
    name: str,
    entry: dict,
    target: Target | Path,
    creature_dir: Path,
) -> bool:
    """Wire ``entry`` into ``config`` unless it already loads ``target``."""
    if uses(config, kind, target, creature_dir):
        return False
    if kind in LIST_KEYS:
        key = LIST_KEYS[kind]
        if not isinstance(config.get(key), list):
            config[key] = CommentedSeq()
        config[key].append(CommentedMap(entry))
    elif kind == "inputs":
        config["input"] = CommentedMap(entry)
    else:
        if not isinstance(config.get("output"), dict):
            config["output"] = CommentedMap()
        output = config["output"]
        if not isinstance(output.get("named_outputs"), dict):
            output["named_outputs"] = CommentedMap()
        output["named_outputs"][name] = CommentedMap(entry)
    return True


def unplug(
    config: CommentedMap, kind: str, target: Target | Path, creature_dir: Path
) -> bool:
    """Remove every entry of ``config`` that loads ``target``."""
    changed = False
    if kind in LIST_KEYS:
        items = config.get(LIST_KEYS[kind])
        if isinstance(items, list):
            keep = [e for e in items if not _loads(e, target, creature_dir)]
            changed = len(keep) != len(items)
            items[:] = keep
        return changed
    if kind == "inputs":
        if _loads(config.get("input"), target, creature_dir):
            del config["input"]
            return True
        return False
    output = config.get("output")
    if not isinstance(output, dict):
        return False
    named = output.get("named_outputs")
    if isinstance(named, dict):
        for key in [k for k, e in named.items() if _loads(e, target, creature_dir)]:
            del named[key]
            changed = True
        if not named:
            del output["named_outputs"]
    if _loads(output, target, creature_dir):
        for key in ("type", "module", "class"):
            output.pop(key, None)
        changed = True
    return changed
