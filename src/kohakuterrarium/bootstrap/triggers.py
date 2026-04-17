"""
Trigger initialization factory.

Creates trigger instances from agent config and registers them
with the trigger manager.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.loader import ModuleLoadError, ModuleLoader
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.modules.trigger import (
    BaseTrigger,
    ChannelTrigger,
    ContextUpdateTrigger,
    TimerTrigger,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def create_trigger(
    trigger_config: Any,
    session: Session | None,
    loader: ModuleLoader | None,
) -> BaseTrigger | None:
    """Create a single trigger from a trigger config entry.

    Handles builtin types (timer, context, channel) and custom/package
    triggers. Returns None if the trigger could not be created.
    """
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
            logger.warning("Unknown trigger type", trigger_type=trigger_config.type)
            return None


def init_triggers(
    config: AgentConfig,
    trigger_manager: TriggerManager,
    session: Session | None,
    loader: ModuleLoader | None,
) -> None:
    """Register all triggers from agent config into the trigger manager.

    Creates triggers and registers them (without starting) so they
    can be started later via trigger_manager.start_all().
    """
    for trigger_config in config.triggers:
        trigger = create_trigger(trigger_config, session, loader)
        if trigger:
            # Prefer an explicit user-provided name as the stable trigger_id
            # (used for inheritance identity, resume, /stop, etc.). Fall back
            # to the auto-generated shape for triggers without a name.
            trigger_id = (
                trigger_config.name
                or f"{trigger_config.type}_{trigger_config.class_name or 'builtin'}"
            )
            # Use sync add via internal dict (not started yet)
            trigger_manager._triggers[trigger_id] = trigger
            trigger_manager._created_at[trigger_id] = datetime.now()
            logger.debug(
                "Registered trigger",
                trigger_id=trigger_id,
                trigger_type=trigger_config.type,
            )
