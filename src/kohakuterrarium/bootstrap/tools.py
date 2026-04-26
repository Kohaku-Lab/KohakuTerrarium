"""
Tool initialization factory.

Registers tools from agent config into the module registry.
"""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
from kohakuterrarium.modules.trigger.universal import list_universal_trigger_classes
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _coerce_tool_config_value(key: str, value: Any) -> Any:
    """Coerce common ToolConfig fields from config-file values."""
    if key == "max_output":
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"max_output must be an integer, got {value!r}")
        if coerced < 0:
            raise ValueError("max_output must be >= 0")
        return coerced
    if key == "timeout":
        try:
            coerced_float = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"timeout must be numeric, got {value!r}")
        if coerced_float < 0:
            raise ValueError("timeout must be >= 0")
        return coerced_float
    return value


# Universal trigger classes the framework ships. `type: trigger` entries
# look up the trigger by its `setup_tool_name`.
def _universal_trigger_classes() -> list[type[BaseTrigger]]:
    return list_universal_trigger_classes()


def _lookup_trigger_class(name: str) -> type[BaseTrigger] | None:
    for cls in _universal_trigger_classes():
        if cls.setup_tool_name == name:
            return cls
    return None


def create_tool(
    tool_config: Any,
    loader: ModuleLoader | None,
) -> BaseTool | None:
    """Create a single tool instance from a tool config entry.

    Handles builtin, custom, and package tool types. Returns None
    if the tool could not be created.
    """
    match tool_config.type:
        case "builtin":
            raw_options = dict(tool_config.options or {})
            tool_cfg_keys = {
                "timeout",
                "max_output",
                "working_dir",
                "env",
                "notify_controller_on_background_complete",
            }
            tool_cfg_values = {}
            try:
                for key in list(raw_options):
                    if key in tool_cfg_keys:
                        tool_cfg_values[key] = _coerce_tool_config_value(
                            key, raw_options.pop(key)
                        )
            except ValueError as exc:
                logger.warning(
                    "Invalid tool config value",
                    tool_name=tool_config.name,
                    error=str(exc),
                )
                return None
            tool_cfg = ToolConfig(**tool_cfg_values, extra=raw_options)
            tool = get_builtin_tool(tool_config.name, config=tool_cfg)
            if tool is None:
                logger.warning("Unknown built-in tool", tool_name=tool_config.name)
            return tool

        case "trigger":
            trigger_cls = _lookup_trigger_class(tool_config.name)
            if trigger_cls is None:
                available = ", ".join(
                    cls.setup_tool_name for cls in _universal_trigger_classes()
                )
                logger.warning(
                    "Unknown setup-able trigger",
                    tool_name=tool_config.name,
                    available=available,
                )
                return None
            return CallableTriggerTool(trigger_cls)

        case "custom" | "package":
            if not tool_config.module or not tool_config.class_name:
                logger.warning(
                    "Custom tool missing module or class",
                    tool_name=tool_config.name,
                )
                return None
            if loader is None:
                logger.warning(
                    "No module loader available for custom tool",
                    tool_name=tool_config.name,
                )
                return None
            try:
                return loader.load_instance(
                    module_path=tool_config.module,
                    class_name=tool_config.class_name,
                    module_type=tool_config.type,
                    options=tool_config.options,
                )
            except ModuleLoadError as e:
                logger.error("Failed to load custom tool", error=str(e))
                return None

        case _:
            logger.warning("Unknown tool type", tool_type=tool_config.type)
            return None


def init_tools(
    config: AgentConfig,
    registry: Registry,
    loader: ModuleLoader | None,
) -> None:
    """Register all tools from agent config into the registry.

    Iterates over config.tools and creates each tool via create_tool(),
    registering successful results in the registry.
    """
    for tool_config in config.tools:
        tool = create_tool(tool_config, loader)
        if tool:
            registry.register_tool(tool)
