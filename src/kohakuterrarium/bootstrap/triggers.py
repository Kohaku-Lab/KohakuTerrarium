"""
Create configured triggers and register them without starting them.

Unknown type names resolve through installed package manifests, allowing
packaged triggers to be referenced without repeating module and class paths.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.modules.trigger import (
    BaseTrigger,
    ChannelTrigger,
    ContextUpdateTrigger,
    TimerTrigger,
)
from kohakuterrarium.packages.resolve import resolve_package_trigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def create_trigger(
    trigger_config: Any,
    session: Session | None,
    loader: ModuleLoader | None,
) -> BaseTrigger | None:
    """Create one built-in, custom, or package-resolved trigger."""
    match trigger_config.type:
        case "timer":
            return TimerTrigger(
                interval=trigger_config.options.get("interval", 60.0),
                prompt=trigger_config.prompt,
                immediate=trigger_config.options.get("immediate", False),
            )

        case "context":
            return ContextUpdateTrigger(
                prompt=trigger_config.prompt,
                debounce_ms=trigger_config.options.get("debounce_ms", 100),
            )

        case "channel":
            return ChannelTrigger(
                channel_name=trigger_config.options.get("channel", ""),
                prompt=trigger_config.prompt,
                filter_sender=trigger_config.options.get("filter_sender"),
                session=session,
            )

        case "custom" | "package":
            if not trigger_config.module or not trigger_config.class_name:
                logger.warning("Custom trigger missing module or class")
                return None
            if loader is None:
                logger.warning(
                    "No module loader available for custom trigger",
                )
                return None
            try:
                trigger = loader.load_instance(
                    module_path=trigger_config.module,
                    class_name=trigger_config.class_name,
                    module_type=trigger_config.type,
                    options={
                        "prompt": trigger_config.prompt,
                        **trigger_config.options,
                    },
                )
                return trigger
            except ModuleLoadError as e:
                logger.error("Failed to load custom trigger", error=str(e))
                return None

        case _:
            # Bare names resolve through package manifests before being rejected.
            package_match = resolve_package_trigger(trigger_config.type)
            if package_match is None:
                logger.warning("Unknown trigger type", trigger_type=trigger_config.type)
                return None
            module_path, class_name = package_match
            if loader is None:
                logger.warning(
                    "No module loader available for packaged trigger",
                    trigger_type=trigger_config.type,
                )
                return None
            try:
                return loader.load_instance(
                    module_path=module_path,
                    class_name=class_name,
                    module_type="package",
                    options={
                        "prompt": trigger_config.prompt,
                        **trigger_config.options,
                    },
                )
            except ModuleLoadError as e:
                logger.error(
                    "Failed to load packaged trigger",
                    trigger_type=trigger_config.type,
                    error=str(e),
                )
                return None


def init_triggers(
    config: AgentConfig,
    trigger_manager: TriggerManager,
    session: Session | None,
    loader: ModuleLoader | None,
) -> None:
    """Register configured triggers for later manager-controlled startup."""
    for trigger_config in config.triggers:
        trigger = create_trigger(trigger_config, session, loader)
        if trigger:
            # Explicit names provide stable identity across inheritance and resume.
            trigger_id = (
                trigger_config.name
                or f"{trigger_config.type}_{trigger_config.class_name or 'builtin'}"
            )
            # Direct registration is safe because startup occurs later.
            trigger_manager._triggers[trigger_id] = trigger
            trigger_manager._created_at[trigger_id] = datetime.now()
            logger.debug(
                "Registered trigger",
                trigger_id=trigger_id,
                trigger_type=trigger_config.type,
            )
