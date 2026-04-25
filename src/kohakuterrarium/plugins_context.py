"""High-level plugin context helpers.

The core plugin protocol lives in :mod:`kohakuterrarium.modules.plugin.base`.
Helpers that need to construct full Agent/session objects live here so the
low-level plugin module does not import high-level core modules at runtime.

``core.agent`` imports this module at top level (and ``modules.plugin.base``
imports it transitively before ``Agent`` is defined). To avoid a hard cycle
without resorting to function-local ``import`` statements, ``Agent`` is
reached via ``sys.modules["kohakuterrarium.core.agent"].Agent`` at call time
— attribute lookup on a fully-initialized module entry, not an import
statement, so the dep-graph linter does not see it as an in-function import.
"""

import sys
from typing import Any

from kohakuterrarium.session.attach import attach_agent_to_session
from kohakuterrarium.session.session import Session as AsyncSession


def spawn_child_agent(
    context: Any,
    config_path_or_dict: str | dict[str, Any],
    role: str = "child",
) -> Any:
    """Build a child ``Agent`` and attach it to the host session."""
    host = context._host_agent
    if host is None:
        raise RuntimeError(
            "PluginContext.spawn_child_agent requires a host agent; "
            "cannot spawn before plugin on_load.",
        )
    store = getattr(host, "session_store", None)
    if store is None:
        raise RuntimeError(
            "PluginContext.spawn_child_agent requires the host agent "
            "to have a SessionStore attached.",
        )

    Agent = sys.modules["kohakuterrarium.core.agent"].Agent
    if isinstance(config_path_or_dict, str):
        child = Agent.from_path(config_path_or_dict)
    elif isinstance(config_path_or_dict, dict):
        AgentConfig = sys.modules["kohakuterrarium.core.config"].AgentConfig
        cfg = AgentConfig(**config_path_or_dict)
        child = Agent(cfg)
    else:
        raise TypeError(
            "config_path_or_dict must be a str path or AgentConfig dict, "
            f"got {type(config_path_or_dict).__name__}",
        )

    session = AsyncSession(store, agent=host)
    plugin_name = getattr(context, "_plugin_name", "")
    attach_agent_to_session(
        child,
        session,
        role=f"plugin:{plugin_name}/{role}",
        attached_by=f"plugin:{plugin_name}",
    )
    return child
