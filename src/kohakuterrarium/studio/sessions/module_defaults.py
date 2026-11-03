"""Initialize new creatures from a single captured module-defaults snapshot."""

from typing import Any

from kohakuterrarium.terrarium.service.service import TerrariumService


async def initialize_modules(
    service: TerrariumService, creature_id: str, defaults: dict[str, Any]
) -> None:
    """Apply defaults to available modules, preserving explicit creature fields."""
    if not defaults:
        return
    for module in await service.list_modules(creature_id):
        kind, name = module["type"], module["name"]
        saved = defaults.get(kind, {}).get(name, {})
        if not saved:
            continue
        configured = module.get("configured_options", {})
        options = {
            key: value
            for key, value in saved.get("options", {}).items()
            if key not in configured
        }
        if kind == "native_tool" and options:
            options = {**options, **configured}
        if options:
            await service.set_module_options(creature_id, kind, name, options)
        if (
            kind == "plugin"
            and "enabled" in saved
            and module.get("configured_enabled") is None
        ):
            await service.toggle_plugin(creature_id, name, saved["enabled"])
