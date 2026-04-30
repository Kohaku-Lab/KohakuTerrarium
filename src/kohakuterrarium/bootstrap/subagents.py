"""
Sub-agent initialization factory.

Registers sub-agent configs from agent config into the sub-agent manager
and module registry.
"""

from typing import Any

from kohakuterrarium.builtins.subagent_catalog import get_builtin_subagent_config
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent import SubAgentManager
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def create_subagent_config(
    item: Any,
    loader: ModuleLoader | None,
) -> SubAgentConfig | None:
    """Create a SubAgentConfig from a config item.

    Handles builtin, custom, and package sub-agent types.
    Returns None if the config could not be created.
    """
    match item.type:
        case "builtin":
            config = get_builtin_subagent_config(item.name)
            if config is None:
                logger.warning("Unknown builtin sub-agent", subagent_name=item.name)
                return None

            # Overlay selected inline options onto builtin config
            if item.options.get("extra_prompt"):
                config.extra_prompt = item.options["extra_prompt"]
            if item.options.get("extra_prompt_file"):
                config.extra_prompt_file = item.options["extra_prompt_file"]
            for field_name in (
                "default_plugins",
                "plugins",
                "compact",
                "model",
            ):
                if field_name in item.options:
                    setattr(config, field_name, item.options[field_name])
            if "notify_controller_on_background_complete" in item.options:
                config.notify_controller_on_background_complete = bool(
                    item.options["notify_controller_on_background_complete"]
                )

            return config

        case "custom" | "package":
            # If module and config_name provided, load from module
            if item.module and item.config_name:
                if loader is None:
                    logger.warning(
                        "No module loader available for custom sub-agent",
                        subagent_name=item.name,
                    )
                    return None
                try:
                    return loader.load_config_object(
                        module_path=item.module,
                        object_name=item.config_name,
                        module_type=item.type,
                    )
                except ModuleLoadError as e:
                    logger.error("Failed to load custom sub-agent", error=str(e))
                    return None

            # Otherwise, create inline config from options. This supports
            # nested YAML-only sub-agent configs without a Python module:
            # ``type: custom`` plus fields like ``system_prompt`` / ``tools``.
            config_dict = {
                "name": item.name,
                "description": item.description or f"{item.name} sub-agent",
                "tools": item.tools,
                "can_modify": item.can_modify,
                "interactive": item.interactive,
                **item.options,
            }
            return SubAgentConfig.from_dict(config_dict)

        case _:
            logger.warning("Unknown sub-agent type", subagent_type=item.type)
            return None


def init_subagents(
    config: AgentConfig,
    subagent_manager: SubAgentManager,
    registry: Registry,
    loader: ModuleLoader | None,
) -> None:
    """Register all sub-agents from agent config.

    Creates SubAgentConfig for each entry in config.subagents,
    registers them with both the sub-agent manager and the module
    registry (so the parser knows about them).
    """
    for subagent_item in config.subagents:
        sa_config = create_subagent_config(subagent_item, loader)
        if sa_config:
            subagent_manager.register(sa_config)
            # Also register with registry so parser knows about it
            registry.register_subagent(sa_config.name, sa_config)

    if subagent_manager.list_subagents():
        logger.info(
            "Sub-agents registered",
            subagents=subagent_manager.list_subagents(),
        )
