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
    """Log the basic plugin and agent lifecycle hooks."""

    name = "hello"
    priority = 50

    async def on_load(self, context: PluginContext) -> None:
        """Capture plugin context and log the initialized agent metadata."""
        self._ctx = context
        logger.info(
            "Hello plugin loaded",
            agent=context.agent_name,
            cwd=str(context.working_dir),
            model=context.model,
        )

    async def on_unload(self) -> None:
        """Log plugin unloading during agent shutdown."""
        logger.info("Hello plugin unloaded")

    async def on_agent_start(self) -> None:
        """Log that all agent runtime modules have started."""
        logger.info("Agent is running — all systems go")

    async def on_agent_stop(self) -> None:
        """Log the beginning of agent shutdown."""
        logger.info("Agent is stopping — goodbye!")
