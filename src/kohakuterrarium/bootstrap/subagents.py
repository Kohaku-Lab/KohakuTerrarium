"""
Resolve and register configured sub-agents.
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


def _apply_option_overrides(config: SubAgentConfig, options: dict[str, Any]) -> None:
    """Apply creature-level overrides regardless of the config's source."""
    if options.get("extra_prompt"):
        config.extra_prompt = options["extra_prompt"]
    if options.get("extra_prompt_file"):
        config.extra_prompt_file = options["extra_prompt_file"]
    for field_name in ("default_plugins", "plugins", "compact", "model"):
        if field_name in options:
            setattr(config, field_name, options[field_name])
    if "notify_controller_on_background_complete" in options:
        config.notify_controller_on_background_complete = bool(
            options["notify_controller_on_background_complete"]
        )


def create_subagent_config(
    item: Any,
    loader: ModuleLoader | None,
) -> SubAgentConfig | None:
    """Resolve a builtin, module-backed, or inline sub-agent config."""
    match item.type:
        case "builtin":
            config = get_builtin_subagent_config(item.name)
            if config is None:
                logger.warning("Unknown builtin sub-agent", subagent_name=item.name)
                return None

            _apply_option_overrides(config, item.options)

            return config

        case "custom" | "package":
            # Module-backed entries use their named config object as the base.
            if item.module and item.config_name:
                if loader is None:
                    logger.warning(
                        "No module loader available for custom sub-agent",
                        subagent_name=item.name,
                    )
                    return None
                try:
                    config = loader.load_config_object(
                        module_path=item.module,
                        object_name=item.config_name,
                        module_type=item.type,
                    )
                except ModuleLoadError as e:
                    logger.error("Failed to load custom sub-agent", error=str(e))
                    return None
                # Inline options override module defaults just as they do built-ins.
                _apply_option_overrides(config, item.options)
                return config

            # A custom entry without a module is a self-contained YAML config.
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
    """Register sub-agents with both execution and parsing registries."""
    for subagent_item in config.subagents:
        sa_config = create_subagent_config(subagent_item, loader)
        if sa_config:
            subagent_manager.register(sa_config)
            # The parser registry must recognize sub-agent call names.
            registry.register_subagent(sa_config.name, sa_config)

    if subagent_manager.list_subagents():
        logger.info(
            "Sub-agents registered",
            subagents=subagent_manager.list_subagents(),
        )
