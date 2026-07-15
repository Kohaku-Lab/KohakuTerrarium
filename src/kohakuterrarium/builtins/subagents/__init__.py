"""Lazily expose built-in sub-agent configurations and catalog helpers."""

import importlib

_SUBAGENT_EXPORTS = {
    "BUILTIN_SUBAGENTS": "kohakuterrarium.builtins.subagent_catalog",
    "get_builtin_subagent_config": "kohakuterrarium.builtins.subagent_catalog",
    "list_builtin_subagents": "kohakuterrarium.builtins.subagent_catalog",
    "COORDINATOR_CONFIG": "kohakuterrarium.builtins.subagents.coordinator",
    "CRITIC_CONFIG": "kohakuterrarium.builtins.subagents.critic",
    "EXPLORE_CONFIG": "kohakuterrarium.builtins.subagents.explore",
    "MEMORY_READ_CONFIG": "kohakuterrarium.builtins.subagents.memory_read",
    "MEMORY_WRITE_CONFIG": "kohakuterrarium.builtins.subagents.memory_write",
    "PLAN_CONFIG": "kohakuterrarium.builtins.subagents.plan",
    "RESEARCH_CONFIG": "kohakuterrarium.builtins.subagents.research",
    "RESPONSE_CONFIG": "kohakuterrarium.builtins.subagents.response",
    "SUMMARIZE_CONFIG": "kohakuterrarium.builtins.subagents.summarize",
    "WORKER_CONFIG": "kohakuterrarium.builtins.subagents.worker",
}

__all__ = list(_SUBAGENT_EXPORTS.keys())


def __getattr__(name: str):
    """Load a configured export on first access and cache it in the module."""
    module_name = _SUBAGENT_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
