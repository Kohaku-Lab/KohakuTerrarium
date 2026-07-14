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

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.trigger.base import BaseTrigger
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
        after ``start()`` used to never receive it).  The aggregated
        system prompt is recomputed so a plugin's ``get_prompt_content``
        contribution enters the live prompt at once (it previously only
        appeared on the next unrelated refresh).
        """
        if self.plugins is None:
            self.plugins = PluginManager()
            if hasattr(self, "controller") and self.controller is not None:
                self.controller.plugins = self.plugins
                self._apply_plugin_hooks()
        name = getattr(plugin, "name", "")
        # Replace any same-named instance so a re-add is idempotent and a
        # plugin a package registered *disabled* becomes this enabled instance
        # instead of a stale duplicate that stays in ``_disabled``.
        if name:
            self.plugins.unregister(name)
        self.plugins.register(plugin)
        if not enabled and name:
            self.plugins.disable(name)
        if enabled and self._running and name:
            # load_all already ran at start() — queue + drain on_load now.
            self.plugins._needs_load.add(name)
            await self.plugins.load_pending()
        # A plugin contributes both a bounded prompt fragment and possibly user
        # (slash) commands; rebuild BOTH so they appear together. If the command
        # aggregation collides, roll the partial install back so a failed add
        # leaves plugin state, prompt, and registry unchanged (R1-23).
        try:
            self.refresh_system_prompt()
            refresh_commands = getattr(self, "refresh_user_commands", None)
            if callable(refresh_commands):
                refresh_commands()
        except Exception:
            if name:
                self.plugins.unregister(name)
            self.refresh_system_prompt()
            raise
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

    async def add_trigger(
        self, trigger: BaseTrigger, trigger_id: str | None = None
    ) -> str:
        """Add and start a trigger on a running agent.

        Returns:
            The trigger_id
        """
        return await self.trigger_manager.add(trigger, trigger_id=trigger_id)

    async def remove_trigger(self, trigger_id_or_trigger: str | BaseTrigger) -> bool:
        """Stop and remove a trigger.

        Args:
            trigger_id_or_trigger: Trigger ID string, or BaseTrigger instance
                                   (for backward compat, searches by identity)

        Returns:
            True if removed
        """
        if isinstance(trigger_id_or_trigger, str):
            return await self.trigger_manager.remove(trigger_id_or_trigger)

        # Backward compat: find by instance identity
        for tid, t in self.trigger_manager._triggers.items():
            if t is trigger_id_or_trigger:
                return await self.trigger_manager.remove(tid)
        return False

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        """Set a custom output handler callback for text chunks."""

        class CallbackOutput(OutputModule):
            def __init__(self, callback: Any):
                self._callback = callback

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def write(self, text: str) -> None:
                self._callback(text)

            async def write_stream(self, chunk: str) -> None:
                self._callback(chunk)

            async def flush(self) -> None:
                pass

            async def on_processing_start(self) -> None:
                pass

            async def on_processing_end(self) -> None:
                pass

            def on_activity(self, activity_type: str, detail: str) -> None:
                pass

        callback_output = CallbackOutput(handler)
        if replace_default:
            self.output_router.default_output = callback_output
        else:
            self.output_router.add_secondary(callback_output)
