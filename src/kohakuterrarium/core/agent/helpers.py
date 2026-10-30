"""Composition helpers attached to ``Agent`` post-construction.

Wiring for agent-scoped helper objects that expose session configuration.

Each helper retains an agent reference while the agent remains the source of
truth for all mutable state.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.core.agent_native_tools import NativeToolOptions
from kohakuterrarium.core.agent_plugin_options import PluginOptions
from kohakuterrarium.core.agent_tool_options import ToolOptions
from kohakuterrarium.core.agent_workspace import WorkspaceController

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent


def attach_session_helpers(agent: "Agent") -> None:
    """Wire all per-agent composition helpers onto the agent instance.

    Called once from ``Agent.__init__``. Adds:

    * ``agent.native_tool_options`` — provider-native tool option
      overrides (see :mod:`agent_native_tools`).
    * ``agent.plugin_options`` — plugin option overrides
      (see :mod:`agent_plugin_options`).
    * ``agent.tool_options`` — ordinary local-tool option overrides
      (see :mod:`agent_tool_options`).
    * ``agent.workspace`` — runtime working-directory switch
      (see :mod:`agent_workspace`).
    """
    agent.native_tool_options = NativeToolOptions(agent)
    agent.plugin_options = PluginOptions(agent)
    agent.tool_options = ToolOptions(agent)
    agent.workspace = WorkspaceController(agent)
