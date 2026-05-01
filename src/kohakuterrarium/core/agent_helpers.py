"""Composition helpers attached to ``Agent`` post-construction.

Tiny wiring module so :class:`~kohakuterrarium.core.agent.Agent` only
spends one line on adding new per-agent helpers — keeping ``agent.py``
under its file-size cap.

Each helper is a thin object that holds an ``agent`` reference and
exposes session-scoped read/write methods (e.g. ``agent.workspace.set``,
``agent.native_tool_options.set``). The agent is the source of truth;
the helpers are stateless beyond what they can re-read off the agent.
"""

from typing import TYPE_CHECKING

from kohakuterrarium.core.agent_native_tools import NativeToolOptions
from kohakuterrarium.core.agent_plugin_options import PluginOptions
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
    * ``agent.workspace`` — runtime working-directory switch
      (see :mod:`agent_workspace`).
    """
    agent.native_tool_options = NativeToolOptions(agent)
    agent.plugin_options = PluginOptions(agent)
    agent.workspace = WorkspaceController(agent)
