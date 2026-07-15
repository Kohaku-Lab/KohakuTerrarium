"""
Core module: fundamental abstractions and runtime components.

Exports the main building blocks for constructing and running agents.
"""

from kohakuterrarium.core.config import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    ToolConfigItem,
    TriggerConfig,
    load_agent_config,
)
from kohakuterrarium.core.controller import (
    Controller,
    ControllerConfig,
    ControllerContext,
)
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_error_event,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.job import (
    JobResult,
    JobState,
    JobStatus,
    JobStore,
    JobType,
    generate_job_id,
)
from kohakuterrarium.core.loader import (
    ModuleLoader,
    ModuleLoadError,
    load_custom_module,
)
from kohakuterrarium.core.registry import Registry

__all__ = [
    "Agent",
    "run_agent",
    "Environment",
    "AgentConfig",
    "InputConfig",
    "OutputConfig",
    "ToolConfigItem",
    "TriggerConfig",
    "load_agent_config",
    "TriggerEvent",
    "EventType",
    "create_user_input_event",
    "create_tool_complete_event",
    "create_error_event",
    "Conversation",
    "ConversationConfig",
    "Controller",
    "ControllerConfig",
    "ControllerContext",
    "Executor",
    "JobStatus",
    "JobResult",
    "JobState",
    "JobType",
    "JobStore",
    "generate_job_id",
    "Registry",
    "ModuleLoader",
    "ModuleLoadError",
    "load_custom_module",
]


def __getattr__(name: str):
    """Resolve the lazily exported ``Agent`` and ``run_agent`` attributes.

    ``builtins.inputs.cli`` imports ``core.events`` via ``core.__init__``;
    eagerly importing ``core.agent`` here would pull in ``bootstrap.io`` which
    imports ``builtins.inputs`` while it is still initialising. Module-level
    ``__getattr__`` is the deliberate exception to the local-import audit.
    """
    if name in ("Agent", "run_agent"):
        from kohakuterrarium.core.agent import Agent, run_agent

        globals()["Agent"] = Agent
        globals()["run_agent"] = run_agent
        return Agent if name == "Agent" else run_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
