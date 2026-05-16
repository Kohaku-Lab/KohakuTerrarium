"""Scope resolvers for ``terrarium.files`` namespace.

Five scopes; each resolves a ``<scope>://<arg>`` URI to an absolute
local path on the node, then optionally appends a relative path that
must stay within the scope root.

| Scope        | Arg form         | Root                                          |
|--------------|------------------|-----------------------------------------------|
| workspace    | <creature_id>    | creature's working directory                  |
| memory       | <creature_id>    | <creature workspace>/memory/                  |
| package      | <package name>   | ``~/.kohakuterrarium/packages/<name>/``        |
| recipe       | <recipe id>      | ``~/.kohakuterrarium/recipes/<id>/`` (staging) |
| config       | (empty)          | ``~/.kohakuterrarium/``                        |

All resolvers enforce three rules on the ``rel`` argument: it must
not be absolute, it must not contain ``..`` segments, and the
resolved absolute path must remain inside the scope root.

Errors surface as :class:`ScopeError` (a ``ValueError``).  Callers in
:mod:`kohakuterrarium.laboratory.adapters.terrarium_files` catch
``ValueError`` and translate to the ``{"error":{"kind":"invalid",…}}``
wire envelope.
"""

import os
from pathlib import Path

from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir


class ScopeError(ValueError):
    """Malformed scope URI or path that escapes its scope root."""


SCOPE_NAMES = ("workspace", "memory", "package", "recipe", "config")


def kt_config_home() -> Path:
    """The node's KohakuTerrarium config root.

    Thin alias over :func:`kohakuterrarium.utils.config_dir.config_dir`
    — the single source of truth for the per-user config root,
    honouring the ``KT_CONFIG_DIR`` environment variable and defaulting
    to ``~/.kohakuterrarium``.  Multi-node is multi-node: two workers
    on one machine (and the test harness) need *isolated* config roots,
    and ``KT_CONFIG_DIR`` provides that without clobbering each other's
    recipe staging / resume drops / package installs.  Resolved fresh
    each call so the override always wins.
    """
    return config_dir()


def parse_scope(scope_uri: str) -> tuple[str, str]:
    """Split ``"<name>://<arg>"`` into ``(name, arg)``.

    ``arg`` may be empty (e.g. ``"config://"``).  Trailing slashes are
    tolerated.
    """
    if "://" not in scope_uri:
        raise ScopeError(f"missing '://' in scope URI: {scope_uri!r}")
    name, _, arg = scope_uri.partition("://")
    if not name:
        raise ScopeError(f"empty scope name: {scope_uri!r}")
    if name not in SCOPE_NAMES:
        raise ScopeError(f"unknown scope {name!r}; expected one of {SCOPE_NAMES}")
    return name, arg.rstrip("/")


def resolve_scope_root(scope_uri: str, engine: Terrarium) -> Path:
    """Return the absolute root directory of ``scope_uri``.

    Raises :class:`ScopeError` for unknown scopes or missing scope
    arguments (e.g. ``workspace://`` with no creature_id).
    """
    name, arg = parse_scope(scope_uri)
    resolver = _RESOLVERS[name]
    return resolver(arg, engine)


def resolve_in_scope(scope_uri: str, rel: str, engine: Terrarium) -> Path:
    """Resolve ``rel`` within ``scope_uri``'s root, guarding traversal.

    Returns the absolute path of the file or directory.  Use this for
    every file operation — never construct paths from raw user input.
    """
    root = resolve_scope_root(scope_uri, engine)
    return _ensure_in_root(root, rel)


# ---------------------------------------------------------------------------
# Per-scope resolvers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _creature_pwd(creature) -> str | None:
    """Best-effort working-dir lookup that copes with the executor-less FakeAgent."""
    executor = getattr(creature.agent, "executor", None)
    if executor is not None and hasattr(executor, "_working_dir"):
        wd = str(executor._working_dir)
        # Real executors always have a non-empty working dir; an empty
        # string usually means a stub agent (tests, partially-initialised
        # agent).  Fall through to ``config.pwd`` in that case rather
        # than returning ``""`` and breaking ``workspace://`` resolution.
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
    """Resolve ``rel`` under ``root`` and assert it stays inside.

    Empty ``rel`` returns the root itself.  Absolute or ``..``-bearing
    inputs are rejected.
    """
    root_resolved = root.resolve()
    if not rel:
        return root_resolved
    p = Path(rel)
    if p.is_absolute():
        raise ScopeError(f"absolute path not allowed in scope: {rel!r}")
    # Normalise separators; reject parent-dir traversal.
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
