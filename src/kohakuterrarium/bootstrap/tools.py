"""
Tool initialization factory.

Registers tools from agent config into the module registry.

``strict`` semantics (E4): programmatic construction defaults to
strict — a misconfigured tool raises :class:`ConfigError` instead of
being warn-skipped, because a silently missing tool turns into "the
agent ran and produced nothing" hours later.  Interactive frontends
(Studio / Lab managed spawns) pass ``strict=False`` to keep
degrade-and-continue.
"""

from typing import Any

from kohakuterrarium.errors import ConfigError
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


def _fail(strict: bool, message: str, **log_fields: Any) -> None:
    """Raise :class:`ConfigError` in strict mode; warn otherwise."""
    if strict:
        raise ConfigError(message)
    logger.warning(message, **log_fields)


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
    *,
    strict: bool = False,
) -> BaseTool | None:
    """Create a single tool instance from a tool config entry.

    Handles builtin, custom, and package tool types. Returns None
    if the tool could not be created (lenient mode); raises
    :class:`ConfigError` instead when ``strict``.
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
                _fail(
                    strict,
                    f"Invalid config value for tool " f"{tool_config.name!r}: {exc}",
                    tool_name=tool_config.name,
                )
                return None
            tool_cfg = ToolConfig(**tool_cfg_values, extra=raw_options)
            tool = get_builtin_tool(tool_config.name, config=tool_cfg)
            if tool is None:
                _fail(
                    strict,
                    f"Unknown built-in tool: {tool_config.name!r}",
                    tool_name=tool_config.name,
                )
            return tool

        case "trigger":
            trigger_cls = _lookup_trigger_class(tool_config.name)
            if trigger_cls is None:
                available = ", ".join(
                    cls.setup_tool_name for cls in _universal_trigger_classes()
                )
                _fail(
                    strict,
                    f"Unknown setup-able trigger {tool_config.name!r} "
                    f"(available: {available})",
                    tool_name=tool_config.name,
                )
                return None
            return CallableTriggerTool(trigger_cls)

        case "custom" | "package":
            if not tool_config.module or not tool_config.class_name:
                _fail(
                    strict,
                    f"Custom tool {tool_config.name!r} is missing " "module or class",
                    tool_name=tool_config.name,
                )
                return None
            if loader is None:
                _fail(
                    strict,
                    f"No module loader available for custom tool "
                    f"{tool_config.name!r}",
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
                if strict:
                    raise ConfigError(
                        f"Failed to load custom tool " f"{tool_config.name!r}: {e}"
                    ) from e
                logger.error("Failed to load custom tool", error=str(e))
                return None

        case _:
            _fail(
                strict,
                f"Unknown tool type {tool_config.type!r} for tool "
                f"{tool_config.name!r}",
                tool_type=tool_config.type,
            )
            return None


def init_tools(
    config: AgentConfig,
    registry: Registry,
    loader: ModuleLoader | None,
    *,
    strict: bool = False,
) -> None:
    """Register all tools from agent config into the registry.

    Iterates over config.tools and creates each tool via create_tool(),
    registering successful results in the registry.  ``strict`` raises
    on the first misconfigured tool instead of skipping it.
    """
    for tool_config in config.tools:
        tool = create_tool(tool_config, loader, strict=strict)
        if tool:
            registry.register_tool(tool)
