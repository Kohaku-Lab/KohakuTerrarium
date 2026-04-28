"""Builtin catalog read-side helpers.

Single source of truth for both the studio catalog routes and the
``kt extension`` CLI formatter — listing builtin tools, sub-agents,
universal triggers, plus per-package extension modules.
"""

from kohakuterrarium.builtin_skills import (
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
)
from kohakuterrarium.builtins.subagent_catalog import (
    get_builtin_subagent_config,
    list_builtin_subagents,
)
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool, list_builtin_tools
from kohakuterrarium.modules.trigger.universal import list_universal_trigger_classes
from kohakuterrarium.packages.walk import get_package_modules, list_packages

_EXTENSION_MODULE_TYPES = ("tools", "plugins", "llm_presets")


# ----------------------------------------------------------------------
# Catalog route data — builtins for tools / subagents / triggers
# ----------------------------------------------------------------------


def list_builtin_tool_entries() -> list[dict]:
    """Return catalog entries for every builtin tool."""
    out: list[dict] = []
    for name in list_builtin_tools():
        tool = get_builtin_tool(name)
        if tool is None:
            continue
        try:
            execution_mode = tool.execution_mode.value
        except Exception:
            execution_mode = "direct"
        out.append(
            {
                "name": name,
                "description": tool.description,
                "source": "builtin",
                "type": "builtin",
                "module": None,
                "class_name": None,
                "execution_mode": execution_mode,
                "needs_context": bool(getattr(tool, "needs_context", False)),
                "require_manual_read": bool(
                    getattr(tool, "require_manual_read", False)
                ),
                "has_doc": get_builtin_tool_doc(name) is not None,
            }
        )
    return out


def list_builtin_subagent_entries() -> list[dict]:
    """Return catalog entries for every builtin sub-agent."""
    out: list[dict] = []
    for name in list_builtin_subagents():
        cfg = get_builtin_subagent_config(name)
        if cfg is None:
            continue
        out.append(
            {
                "name": name,
                "description": cfg.description,
                "source": "builtin",
                "type": "builtin",
                "module": None,
                "class_name": None,
                "can_modify": bool(cfg.can_modify),
                "interactive": bool(cfg.interactive),
                "tools": list(cfg.tools),
                "has_doc": get_builtin_subagent_doc(name) is not None,
            }
        )
    return out


def list_universal_trigger_entries() -> list[dict]:
    """Return catalog entries for every universal setup-tool trigger."""
    out: list[dict] = []
    for cls in list_universal_trigger_classes():
        if not getattr(cls, "universal", False):
            continue
        out.append(
            {
                "name": cls.setup_tool_name,
                "description": cls.setup_description,
                "source": "builtin",
                "type": "trigger",
                "module": None,
                "class_name": None,
                "param_schema": cls.setup_param_schema,
                "require_manual_read": bool(cls.setup_require_manual_read),
            }
        )
    return out


def get_tool_doc(name: str) -> str | None:
    """Return the builtin skill doc for *name* (or None)."""
    return get_builtin_tool_doc(name)


def get_subagent_doc(name: str) -> str | None:
    """Return the builtin skill doc for sub-agent *name* (or None)."""
    return get_builtin_subagent_doc(name)


# ----------------------------------------------------------------------
# CLI extension formatter data
# ----------------------------------------------------------------------


def list_extension_packages() -> list[dict]:
    """Return every installed package with its raw manifest dict.

    Used by ``kt extension list`` — the CLI just formats the output
    rather than re-implementing list/walk semantics.
    """
    return list_packages()


def get_extension_modules(pkg_name: str, module_type: str) -> list:
    """Return the *module_type* entries declared by *pkg_name*."""
    return get_package_modules(pkg_name, module_type)


def extension_module_types() -> tuple[str, ...]:
    """Module-type tuple in the order the CLI surfaces them."""
    return _EXTENSION_MODULE_TYPES


# ----------------------------------------------------------------------
# Programmatic Studio dispatch — kind-aware list / info aggregators
# ----------------------------------------------------------------------


def list_builtins(kind: str | None = None) -> list[dict]:
    """List builtin catalog entries by *kind*.

    *kind* is one of ``"tools"``, ``"subagents"``, ``"triggers"`` —
    or ``None`` for the union of all three.  Used by
    :class:`kohakuterrarium.studio.Studio` and the catalog HTTP route.
    """
    match kind:
        case "tools" | "tool":
            return list_builtin_tool_entries()
        case "subagents" | "subagent":
            return list_builtin_subagent_entries()
        case "triggers" | "trigger":
            return list_universal_trigger_entries()
        case None:
            return (
                list_builtin_tool_entries()
                + list_builtin_subagent_entries()
                + list_universal_trigger_entries()
            )
        case _:
            raise ValueError(
                f"Unknown builtin kind: {kind!r} "
                "(expected tools / subagents / triggers / None)"
            )


def builtin_info(name: str) -> dict | None:
    """Return the catalog entry for builtin *name*, or ``None``.

    Searches tools, then sub-agents, then universal triggers.
    """
    for entry in list_builtin_tool_entries():
        if entry["name"] == name:
            return entry
    for entry in list_builtin_subagent_entries():
        if entry["name"] == name:
            return entry
    for entry in list_universal_trigger_entries():
        if entry["name"] == name:
            return entry
    return None
