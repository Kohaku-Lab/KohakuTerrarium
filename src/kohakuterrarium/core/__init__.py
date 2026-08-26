"""Core agent abstractions and runtime components public facade."""

import importlib

_EXPORTS = {
    "Agent": "kohakuterrarium.core.agent",
    "AgentConfig": "kohakuterrarium.core.config",
    "Controller": "kohakuterrarium.core.controller",
    "ControllerConfig": "kohakuterrarium.core.controller",
    "ControllerContext": "kohakuterrarium.core.controller",
    "Conversation": "kohakuterrarium.core.conversation",
    "ConversationConfig": "kohakuterrarium.core.conversation",
    "Environment": "kohakuterrarium.core.environment",
    "EventType": "kohakuterrarium.core.events",
    "Executor": "kohakuterrarium.core.executor",
    "InputConfig": "kohakuterrarium.core.config",
    "JobResult": "kohakuterrarium.core.job",
    "JobState": "kohakuterrarium.core.job",
    "JobStatus": "kohakuterrarium.core.job",
    "JobStore": "kohakuterrarium.core.job",
    "JobType": "kohakuterrarium.core.job",
    "ModuleLoadError": "kohakuterrarium.core.loader",
    "ModuleLoader": "kohakuterrarium.core.loader",
    "OutputConfig": "kohakuterrarium.core.config",
    "Registry": "kohakuterrarium.core.registry",
    "ToolConfigItem": "kohakuterrarium.core.config",
    "TriggerConfig": "kohakuterrarium.core.config",
    "TriggerEvent": "kohakuterrarium.core.events",
    "create_error_event": "kohakuterrarium.core.events",
    "create_tool_complete_event": "kohakuterrarium.core.events",
    "create_user_input_event": "kohakuterrarium.core.events",
    "generate_job_id": "kohakuterrarium.core.job",
    "load_agent_config": "kohakuterrarium.core.config",
    "load_custom_module": "kohakuterrarium.core.loader",
    "run_agent": "kohakuterrarium.core.agent",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
