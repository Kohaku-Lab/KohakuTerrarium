"""Tool and plugin defaults used to initialize new Studio sessions."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.builtins.tool_catalog import list_configurable_tools
from kohakuterrarium.core.tools.native_validation import validate_native_tool_options
from kohakuterrarium.modules.plugin.option_validation import validate_plugin_options
from kohakuterrarium.modules.tool.runtime_options import validate_tool_options
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.file.lock import FileLock


def defaults_path() -> Path:
    """Return the server's new-session module defaults file."""
    return config_dir() / "module-defaults.json"


def load_defaults() -> dict[str, Any]:
    """Read explicitly saved defaults; a missing file means no overrides."""
    path = defaults_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Module defaults must be an object")
    return data


def module_catalog() -> list[dict]:
    """List configurable tools and discovered plugins without starting an agent."""
    modules = list_configurable_tools()
    for plugin in init_plugins([]).list_plugins_with_options():
        modules.append({"type": "plugin", **plugin})
    return modules


def list_defaults() -> list[dict]:
    """Return editor entries with saved defaults merged over built-in values."""
    saved = load_defaults()
    modules = module_catalog()
    for module in modules:
        entry = saved.get(module["type"], {}).get(module["name"], {})
        module["options"] = {**module["options"], **entry.get("options", {})}
        if module["type"] == "plugin" and "enabled" in entry:
            module["enabled"] = entry["enabled"]
    return modules


def patch_defaults(
    module_type: str, name: str, values: dict[str, Any], *, enabled: bool | None = None
) -> dict:
    """Validate and atomically patch one module without changing other fields."""
    module = next(
        (m for m in module_catalog() if m["type"] == module_type and m["name"] == name),
        None,
    )
    if module is None:
        raise KeyError(name)
    if enabled is not None and module_type != "plugin":
        raise ValueError("Only plugins support enablement")
    validators = {
        "tool": validate_tool_options,
        "native_tool": validate_native_tool_options,
        "plugin": validate_plugin_options,
    }
    validators[module_type](name, values, module["schema"])
    path = defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock")):
        saved = load_defaults()
        entry = saved.setdefault(module_type, {}).setdefault(name, {})
        candidate = {**entry.get("options", {}), **values}
        cleaned = validators[module_type](name, candidate, module["schema"])
        if module_type == "plugin" and cleaned:
            init_plugins([]).set_plugin_options(name, cleaned)
        entry["options"] = cleaned
        if enabled is not None:
            entry["enabled"] = enabled
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(saved, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    return {
        "options": {**module["options"], **cleaned},
        "enabled": entry.get("enabled", module["enabled"]),
    }
