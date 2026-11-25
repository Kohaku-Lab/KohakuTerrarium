"""
Resolve configured tools and register them with the agent.

Strict mode rejects misconfigured tools; lenient interactive construction logs
and skips them.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.config.load import AgentConfig
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ToolConfig
from kohakuterrarium.modules.tool.origin import BUILTIN_ORIGIN, ToolOrigin
from kohakuterrarium.modules.tool.request_replay import validate_request_replay
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
from kohakuterrarium.modules.trigger.universal import list_universal_trigger_classes
from kohakuterrarium.packages.resolve import package_for_module, package_ref
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _fail(strict: bool, message: str, **log_fields: Any) -> None:
    """Raise configuration failures in strict mode and log them otherwise."""
    if strict:
        raise ConfigError(message)
    logger.warning(message, **log_fields)


def _coerce_tool_config_value(key: str, value: Any) -> Any:
    """Normalize typed ToolConfig fields loaded from serialized values."""
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


# Trigger tools are addressed by each universal trigger's setup name.
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
    """Create one built-in, trigger-backed, custom, or package tool.

    Lenient mode returns ``None`` on failure; strict mode raises
    :class:`ConfigError`.
    """
    raw_options = dict(tool_config.options or {})
    replay_override = None
    if "request_replay" in raw_options:
        try:
            replay_override = validate_request_replay(raw_options.pop("request_replay"))
        except ValueError as exc:
            _fail(strict, f"Invalid config value for tool {tool_config.name!r}: {exc}")
            return None
    match tool_config.type:
        case "builtin":
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
            tool_cfg = ToolConfig(
                **tool_cfg_values,
                doc_mode=tool_config.doc_mode,
                extra=raw_options,
                request_replay=replay_override,
            )
            tool = get_builtin_tool(tool_config.name, config=tool_cfg)
            if tool is None:
                _fail(
                    strict,
                    f"Unknown built-in tool: {tool_config.name!r}",
                    tool_name=tool_config.name,
                )
            else:
                try:
                    tool.validate_runtime_options()
                except ValueError as exc:
                    _fail(
                        strict,
                        f"Invalid runtime options for tool "
                        f"{tool_config.name!r}: {exc}",
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
            tool = CallableTriggerTool(trigger_cls)
            tool.config.request_replay = replay_override
            return tool

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
                tool = loader.load_instance(
                    module_path=tool_config.module,
                    class_name=tool_config.class_name,
                    module_type=tool_config.type,
                    options=raw_options,
                )
                if replay_override is not None:
                    if isinstance(tool, BaseTool):
                        tool.config.request_replay = replay_override
                    else:
                        tool.request_replay = replay_override
                return tool
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


def config_tool_origin(tool_config: Any, config: AgentConfig) -> ToolOrigin:
    """Origin of a tool declared in a creature config.

    ``package`` and ``custom`` tools shipped by an installed package are
    ``package:<pkg>``; other ``custom`` tools are ``custom:<creature>``; a
    ``package`` module no installed package claims is labelled by its
    top-level module name. Built-in and trigger tools are ``builtin``.
    """
    module = tool_config.module or ""
    match tool_config.type:
        case "package":
            name = package_for_module(module) or module.split(".", 1)[0]
            return ToolOrigin("package", name)
        case "custom":
            name = _package_for_file(module, config.agent_path)
            if name:
                return ToolOrigin("package", name)
            return ToolOrigin("custom", config.name or "")
        case _:
            return BUILTIN_ORIGIN


def _package_for_file(module: str, agent_path: Path | None) -> str:
    """Installed package holding a custom tool file; empty when none does."""
    if module.startswith("@"):
        return module[1:].split("/", 1)[0]
    if not module:
        return ""
    path = Path(module).expanduser()
    if not path.is_absolute():
        if agent_path is None:
            return ""
        path = Path(agent_path) / path
    ref = package_ref(path)
    if ref is None:
        return ""
    return ref[1:].split("/", 1)[0]


def init_tools(
    config: AgentConfig,
    registry: Registry,
    loader: ModuleLoader | None,
    *,
    strict: bool = False,
) -> None:
    """Create and register configured tools, honoring strict failure semantics."""
    for tool_config in config.tools:
        tool = create_tool(tool_config, loader, strict=strict)
        if tool:
            registry.register_tool(tool, origin=config_tool_origin(tool_config, config))
