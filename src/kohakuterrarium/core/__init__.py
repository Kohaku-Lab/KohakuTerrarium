"""Core agent abstractions and runtime components public facade."""

import importlib

_EXPORTS = {
    "Agent": "kohakuterrarium.core.agent",
    "AgentConfig": "kohakuterrarium.core.config.load",
    "Controller": "kohakuterrarium.core.controller.controller",
    "ControllerConfig": "kohakuterrarium.core.controller.controller",
    "ControllerContext": "kohakuterrarium.core.controller.controller",
    "Conversation": "kohakuterrarium.core.conversation.conversation",
    "ConversationConfig": "kohakuterrarium.core.conversation.conversation",
    "Environment": "kohakuterrarium.core.environment",
    "EventType": "kohakuterrarium.core.events",
    "Executor": "kohakuterrarium.core.jobs.executor",
    "InputConfig": "kohakuterrarium.core.config.load",
    "JobResult": "kohakuterrarium.core.jobs.job",
    "JobState": "kohakuterrarium.core.jobs.job",
    "JobStatus": "kohakuterrarium.core.jobs.job",
    "JobStore": "kohakuterrarium.core.jobs.job",
    "JobType": "kohakuterrarium.core.jobs.job",
    "ModuleLoadError": "kohakuterrarium.core.loader",
    "ModuleLoader": "kohakuterrarium.core.loader",
    "OutputConfig": "kohakuterrarium.core.config.load",
    "Registry": "kohakuterrarium.core.registry",
    "ToolConfigItem": "kohakuterrarium.core.config.load",
    "TriggerConfig": "kohakuterrarium.core.config.load",
    "TriggerEvent": "kohakuterrarium.core.events",
    "create_error_event": "kohakuterrarium.core.events",
    "create_tool_complete_event": "kohakuterrarium.core.events",
    "create_user_input_event": "kohakuterrarium.core.events",
    "generate_job_id": "kohakuterrarium.core.jobs.job",
    "load_agent_config": "kohakuterrarium.core.config.load",
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
