"""Resolve ``terrarium.files`` scope URIs to constrained local paths.

Workspace and memory scopes are creature-relative; package, recipe, and config
scopes live under the node's configuration root. Relative paths must remain
inside the resolved root and may not be absolute or contain parent traversal.
"""

import os
from pathlib import Path

from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir


class ScopeError(ValueError):
    """Malformed scope URI or path that escapes its scope root."""


SCOPE_NAMES = ("workspace", "memory", "package", "recipe", "config")


def kt_config_home() -> Path:
    """Return the node's current configuration root.

    Resolving on every call honors runtime ``KT_CONFIG_DIR`` changes and allows
    colocated workers to use isolated package, recipe, and resume storage.
    """
    return config_dir()


def parse_scope(scope_uri: str) -> tuple[str, str]:
    """Split a supported scope URI into its name and optional argument."""
    if "://" not in scope_uri:
        raise ScopeError(f"missing '://' in scope URI: {scope_uri!r}")
    name, _, arg = scope_uri.partition("://")
    if not name:
        raise ScopeError(f"empty scope name: {scope_uri!r}")
    if name not in SCOPE_NAMES:
        raise ScopeError(f"unknown scope {name!r}; expected one of {SCOPE_NAMES}")
    return name, arg.rstrip("/")


def resolve_scope_root(scope_uri: str, engine: Terrarium) -> Path:
    """Return the absolute root directory represented by a scope URI."""
    name, arg = parse_scope(scope_uri)
    resolver = _RESOLVERS[name]
    return resolver(arg, engine)


def resolve_in_scope(scope_uri: str, rel: str, engine: Terrarium) -> Path:
    """Resolve a relative path within a scope without allowing traversal."""
    root = resolve_scope_root(scope_uri, engine)
    return _ensure_in_root(root, rel)


def _resolve_workspace(arg: str, engine: Terrarium) -> Path:
    if not arg:
        raise ScopeError("workspace scope requires a creature_id: workspace://<cid>")
    creature = engine.get_creature(arg)
    pwd = _creature_pwd(creature)
    if pwd is None:
        raise ScopeError(f"creature {arg!r} has no working directory configured")
    return Path(pwd)


def _resolve_memory(arg: str, engine: Terrarium) -> Path:
    if not arg:
        raise ScopeError("memory scope requires a creature_id: memory://<cid>")
    creature = engine.get_creature(arg)
    pwd = _creature_pwd(creature)
    if pwd is None:
        raise ScopeError(f"creature {arg!r} has no working directory configured")
    return Path(pwd) / "memory"


def _resolve_package(arg: str, engine: Terrarium) -> Path:
    if not arg:
        raise ScopeError("package scope requires a name: package://<name>")
    base = kt_config_home() / "packages" / arg
    if not base.exists():
        raise ScopeError(f"package {arg!r} not installed at {base}")
    return base


def _resolve_recipe(arg: str, engine: Terrarium) -> Path:
    if not arg:
        raise ScopeError("recipe scope requires an id: recipe://<id>")
    base = kt_config_home() / "recipes" / arg
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_config(arg: str, engine: Terrarium) -> Path:
    if arg:
        raise ScopeError(f"config scope takes no argument; got config://{arg!r}")
    base = kt_config_home()
    base.mkdir(parents=True, exist_ok=True)
    return base


_RESOLVERS = {
    "workspace": _resolve_workspace,
    "memory": _resolve_memory,
    "package": _resolve_package,
    "recipe": _resolve_recipe,
    "config": _resolve_config,
}


def _creature_pwd(creature) -> str | None:
    """Find a creature working directory across complete and partial agents."""
    executor = getattr(creature.agent, "executor", None)
    if executor is not None and hasattr(executor, "_working_dir"):
        wd = str(executor._working_dir)
        # Empty executor paths occur during partial initialization; use config fallbacks.
        if wd:
            return wd
    cfg = getattr(creature.agent, "config", None)
    if cfg is not None:
        pwd = getattr(cfg, "pwd", None)
        if pwd:
            return str(pwd)
        path = getattr(cfg, "agent_path", None)
        if path:
            return str(path)
    return None


def _ensure_in_root(root: Path, rel: str) -> Path:
    """Resolve a relative path under a root and reject escaping inputs."""
    root_resolved = root.resolve()
    if not rel:
        return root_resolved
    p = Path(rel)
    if p.is_absolute():
        raise ScopeError(f"absolute path not allowed in scope: {rel!r}")
    # Normalize both platform separators before checking parent traversal.
    parts = [seg for seg in str(p).replace("\\", "/").split("/") if seg]
    if ".." in parts:
        raise ScopeError(f"'..' segment not allowed in scope path: {rel!r}")
    target = (root / os.path.join(*parts)).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as e:
        raise ScopeError(f"path {rel!r} escapes scope root {root_resolved}") from e
    return target


__all__ = [
    "SCOPE_NAMES",
    "ScopeError",
    "kt_config_home",
    "parse_scope",
    "resolve_in_scope",
    "resolve_scope_root",
]
