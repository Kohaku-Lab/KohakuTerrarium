"""Hello Plugin — the simplest possible plugin.

Demonstrates:
  - on_load: receive PluginContext, access agent_name, working_dir
  - on_agent_start: called after agent.start() completes
  - on_agent_stop: called before agent.stop() begins

Usage in config.yaml:
    plugins:
      - name: hello
        type: custom
        module: examples.plugins.hello_plugin
        class: HelloPlugin
"""

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class HelloPlugin(BasePlugin):
    name = "hello"
    priority = 50

    async def on_load(self, context: PluginContext) -> None:
        """Called once when the plugin is loaded during agent initialization.

        The context provides agent metadata and utility methods.
        Store it if you need it in later hooks.
        """
        self._ctx = context
        logger.info(
            "Hello plugin loaded",
            agent=context.agent_name,
            cwd=str(context.working_dir),
            model=context.model,
        )

    async def on_unload(self) -> None:
        """Called when the agent shuts down. Clean up resources here."""
        logger.info("Hello plugin unloaded")

    async def on_agent_start(self) -> None:
        """Called after agent.start() — all tools, sub-agents, triggers ready."""
        logger.info("Agent is running — all systems go")

    async def on_agent_stop(self) -> None:
        """Called before agent.stop() — agent is about to shut down."""
        logger.info("Agent is stopping — goodbye!")
