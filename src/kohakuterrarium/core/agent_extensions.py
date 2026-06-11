"""Runtime extension injection — ``add_tool`` / ``add_plugin`` /
``add_subagent`` / ``refresh_system_prompt`` (E7).

Before this mixin, registering a tool after construction took TWO
undocumented writes (registry + executor) and the tool never entered
the frozen system prompt; a plugin registered after ``start()`` never
received ``on_load``.  Each ``add_*`` here does the FULL bookkeeping.

Constructor-time injection (``Agent.build(tools=[...], plugins=[...],
subagents=[...], user_commands={...}, outputs={...})``) lands the
instances before the prompt is aggregated, so they appear in the
initial system prompt with zero extra calls.
"""

from typing import Any

from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentExtensionsMixin:
    """Mixin providing runtime extension registration for ``Agent``."""

    def add_tool(self, tool: Any) -> None:
        """Register a tool instance — registry + executor + prompt.

        Idempotent on ``tool_name``: re-adding replaces the previous
        instance.  The system prompt's tool list is recomputed so the
        controller actually SEES the new tool (the old two-step
        registry/executor dance left the prompt frozen).
        """
        self.registry.register_tool(tool)
        self.executor.register_tool(tool)
        self.refresh_system_prompt()
        logger.info("Tool added at runtime", tool_name=tool.tool_name)

    async def add_plugin(self, plugin: Any, *, enabled: bool = True) -> None:
        """Register a plugin instance with full lifecycle wiring.

        The plugin joins the manager (created on demand), its hooks
        apply to the live controller, and — when the agent is already
        running — its ``on_load`` fires immediately (plugins registered
        after ``start()`` used to never receive it).
        """
        if self.plugins is None:
            self.plugins = PluginManager()
            if hasattr(self, "controller") and self.controller is not None:
                self.controller.plugins = self.plugins
                self._apply_plugin_hooks()
        self.plugins.register(plugin)
        name = getattr(plugin, "name", "")
        if not enabled and name:
            self.plugins.disable(name)
        if enabled and self._running and name:
            # load_all already ran at start() — queue + drain on_load now.
            self.plugins._needs_load.add(name)
            await self.plugins.load_pending()
        logger.info("Plugin added at runtime", plugin_name=name or "?")

    def add_subagent(self, config: Any) -> None:
        """Register a :class:`SubAgentConfig` instance + refresh prompt."""
        self.subagent_manager.register(config)
        self.refresh_system_prompt()
        logger.info(
            "Sub-agent added at runtime",
            subagent=getattr(config, "name", "?"),
        )

    def refresh_system_prompt(self) -> None:
        """Recompute the aggregated system prompt from current state.

        Re-runs the same aggregation as construction (tool list,
        sub-agent section, hints) and swaps it into the live
        conversation's system message.  Engine-managed prompt blocks
        (the runtime-graph section) are re-applied by their owners on
        the next engine event.
        """
        system_prompt = self._build_aggregated_prompt()
        self._controller_config.system_prompt = system_prompt
        controller = getattr(self, "controller", None)
        conversation = getattr(controller, "conversation", None)
        if conversation is None:
            return
        get_system = getattr(conversation, "get_system_message", None)
        sys_msg = get_system() if callable(get_system) else None
        if sys_msg is not None:
            sys_msg.content = system_prompt
