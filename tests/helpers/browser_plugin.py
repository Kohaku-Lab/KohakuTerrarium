"""The ``browser`` plugin wired to the scripted driver, and contexts for calling its tools.

``scripted_plugin`` builds a plugin whose sessions launch ``ScriptedDriver``
browsers with short timeouts; ``call`` runs one tool the way the executor
does for this plugin alone: its ``pre_tool_execute`` hook, then ``execute``.
"""

import functools
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.browser.session import BrowserSession
from kohakuterrarium.builtins.plugins.browser.plugin import BrowserPlugin
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.tool.base import ToolContext, ToolResult
from tests.helpers.browser_driver import ScriptedDriver
from tests.unit.browser.test_session import shop

FAST = {"action_timeout_s": 0.3, "nav_timeout_s": 0.5}


def scripted_plugin(setup=shop, **options: Any) -> tuple[BrowserPlugin, ScriptedDriver]:
    """A ``BrowserPlugin`` whose sessions use one ``ScriptedDriver``; not loaded yet."""
    driver = ScriptedDriver(setup)
    plugin = BrowserPlugin(options={**FAST, **options})
    plugin.session_factory = functools.partial(
        BrowserSession, driver=driver, close_stage_timeout=0.1, settle_grace=0.001
    )
    return plugin, driver


def plugin_context(
    working_dir: Path, manager: Any = None, environment: Any = None
) -> PluginContext:
    """A load context whose host agent carries ``manager`` and ``environment``."""
    agent = SimpleNamespace(plugins=manager, environment=environment)
    return PluginContext(
        agent_name="browser_test", working_dir=working_dir, _host_agent=agent
    )


def tool_context(
    plugin: BrowserPlugin,
    working_dir: Path,
    *,
    services: dict[str, Any] | None = None,
    guard: Any = None,
    environment: Any = None,
) -> ToolContext:
    """A tool context whose runtime services are ``services`` plus the plugin's."""
    context = ToolContext(
        agent_name="browser_test",
        session=None,
        working_dir=working_dir,
        path_guard=guard,
        environment=environment,
    )
    context.runtime_services.update(services or {})
    context.runtime_services.update(plugin.runtime_services(context))
    return context


def tool_of(plugin: BrowserPlugin, name: str) -> Any:
    """The plugin's contributed tool named ``name``."""
    return {tool.tool_name: tool for tool in plugin.contribute_tools()}[name]


async def call(
    plugin: BrowserPlugin, name: str, args: dict[str, Any], context: ToolContext
) -> ToolResult:
    """Run tool ``name`` with the plugin's pre-hook applied, as the executor would."""
    tool = tool_of(plugin, name)
    changed = await plugin.pre_tool_execute(
        dict(args), tool_name=name, tool=tool, context=context
    )
    return await tool.execute(changed if changed is not None else dict(args), context)
