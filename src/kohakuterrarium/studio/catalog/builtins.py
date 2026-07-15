"""Builtin catalog read-side helpers.

Builds the shared catalog payloads used by Studio, HTTP routes, and the
``kt extension`` CLI for built-in tools, sub-agents, universal triggers, Drive
registrations, and package extension declarations.
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

_EXTENSION_MODULE_TYPES = ("tools", "plugins", "llm_presets", "drive_registrations")

# Keep Drive registration metadata static so lightweight catalog and CLI calls do
# not import the Terrarium engine. Tests pin these records to the authoritative
# descriptors; only the opt-in ``goal`` registration needs an import target.
_BUILTIN_DRIVE_REGISTRATIONS: tuple[dict, ...] = (
    {
        "name": "generic",
        "kind": "generic",
        "schema_version": 1,
        "source": "builtin",
        "type": "drive-registration",
        "module": None,
        "class_name": None,
        "description": "Opaque-spec drive with manual terminal proposals.",
    },
    {
        "name": "goal",
        "kind": "goal",
        "schema_version": 1,
        "source": "builtin",
        "type": "drive-registration",
        "module": "kohakuterrarium.terrarium.drive.goal",
        "class_name": "GoalDriveRegistration",
        "description": "Durable objective pursuit policy.",
    },
)


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


def list_builtin_drive_registration_entries() -> list[dict]:
    """Return copy-isolated catalog entries for built-in Drive registrations."""
    return [dict(e) for e in _BUILTIN_DRIVE_REGISTRATIONS]


def get_tool_doc(name: str) -> str | None:
    """Return the built-in tool documentation for *name*, if available."""
    return get_builtin_tool_doc(name)


def get_subagent_doc(name: str) -> str | None:
    """Return the built-in sub-agent documentation for *name*, if available."""
    return get_builtin_subagent_doc(name)


def list_extension_packages() -> list[dict]:
    """Return installed package manifests for presentation by CLI adapters.

    Keeping package traversal here prevents each consumer from implementing
    subtly different discovery rules.
    """
    return list_packages()


def get_extension_modules(pkg_name: str, module_type: str) -> list:
    """Return the *module_type* entries declared by *pkg_name*."""
    return get_package_modules(pkg_name, module_type)


def extension_module_types() -> tuple[str, ...]:
    """Module-type tuple in the order the CLI surfaces them."""
    return _EXTENSION_MODULE_TYPES


def list_builtins(kind: str | None = None) -> list[dict]:
    """List built-in catalog entries for a supported kind.

    ``None`` returns the default union of tools, sub-agents, and triggers; Drive
    registrations remain opt-in because their payload shape is distinct.
    """
    match kind:
        case "tools" | "tool":
            return list_builtin_tool_entries()
        case "subagents" | "subagent":
            return list_builtin_subagent_entries()
        case "triggers" | "trigger":
            return list_universal_trigger_entries()
        case "drive_registrations" | "drive_registration" | "drive-registration":
            return list_builtin_drive_registration_entries()
        case None:
            return (
                list_builtin_tool_entries()
                + list_builtin_subagent_entries()
                + list_universal_trigger_entries()
            )
        case _:
            raise ValueError(
                f"Unknown builtin kind: {kind!r} "
                "(expected tools / subagents / triggers / drive_registrations / None)"
            )


def builtin_info(name: str) -> dict | None:
    """Return the first built-in catalog entry matching *name*.

    Lookup order is tools, sub-agents, triggers, then Drive registrations, making
    collisions deterministic across heterogeneous catalogs.
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
    for entry in list_builtin_drive_registration_entries():
        if entry["name"] == name:
            return entry
    return None
