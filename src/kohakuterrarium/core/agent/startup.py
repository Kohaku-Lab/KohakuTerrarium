"""Agent start-up: module start order, wiring, MCP, compaction and plugins."""

import asyncio
from pathlib import Path

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.core.agent.history.compact import (
    restore_compact_state_from_session,
    restore_resume_compact_state,
)
from kohakuterrarium.core.agent.history.selection import track_plugin_selection
from kohakuterrarium.core.agent.mcp import init_mcp, inject_mcp_tools_into_prompt
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.controller_plugins import register_plugin_and_package_commands
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.plugins_context import spawn_child_agent
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir

logger = get_logger(__name__)


class AgentStartupMixin:
    """Bring a built agent's modules up: ``start`` and the steps it runs."""

    async def start(self) -> None:
        """Start all agent modules."""
        logger.info("Starting agent", agent_name=self.config.name)

        # Stash the running loop so cross-thread schedulers (TUI promote)
        # can ``call_soon_threadsafe`` on it without ``get_event_loop()``
        # (which raises in sync contexts on Python 3.14+).
        self._loop = asyncio.get_running_loop()
        self._configure_tui_tabs()

        await self.input.start()
        await self.output_router.start()

        # Wire TUI callbacks (Escape → interrupt, click → cancel/promote)
        tui_input = getattr(self.input, "_tui", None)
        if tui_input and tui_input._app:
            tui_input._app.on_interrupt = self.interrupt
        if tui_input:
            tui_input.on_cancel_job = self._cancel_job
            tui_input.on_promote_job = self._promote_handle

        self._wire_trigger_notifications()
        await self.trigger_manager.start_all()
        self._wire_completion_callbacks()

        self._running = True
        self._paused = False
        self._stopped = False
        self._shutdown_event.clear()
        # Fresh run: the startup trigger has not settled yet.
        self._startup_settled.clear()

        # Initialize MCP client manager if mcp_servers configured
        await self._init_mcp()
        self._inject_mcp_tools_into_prompt()

        self._init_compact_manager()
        if self.plugins and self.compact_manager is not None:
            self.compact_manager._plugins = self.plugins
        await self._load_plugins()
        self._publish_session_info()

        # The single event consumer starts last: events queued while the
        # modules above were loading are processed with every module ready.
        self._consumer_resume.set()
        self._consumer_task = asyncio.create_task(
            self._run_event_consumer(), name=f"event_consumer_{self.config.name}"
        )

        if self._termination_checker:
            self._termination_checker.start()

    def _configure_tui_tabs(self) -> None:
        """Configure TUI with terrarium tabs if available (set by the
        engine TUI launcher).

        ``terrarium.engine_cli.run_engine_with_tui`` writes tab /
        runtime info onto ``session.extra`` before invoking the agent
        loop. This method is a no-op hook that confirms the data is
        already in place for ``TUIInput`` to read.
        """
        # Data is written to session.extra by the engine TUI launcher
        # before agent.run_forever() -> agent.start() -> here, so nothing
        # needs to be copied. Just verify presence for debug logging.
        terrarium_tabs = self.session.extra.get("terrarium_tui_tabs")
        if terrarium_tabs and hasattr(self.input, "_tui"):
            logger.debug(
                "Terrarium TUI tabs configured via session.extra",
                tab_count=len(terrarium_tabs),
            )

    def _wire_trigger_notifications(self) -> None:
        """Wire trigger fired notifications to the output router."""

        def _on_trigger_fired(trigger_id, event):
            ctx = event.context or {}
            channel = ctx.get("channel", "")
            sender = ctx.get("sender", "")
            raw_content = ctx.get("raw_content", "")
            detail = f"[{trigger_id}] channel={channel} sender={sender}"
            self.output_router.notify_activity(
                "trigger_fired",
                detail,
                metadata={
                    "trigger_id": trigger_id,
                    "ui_name": getattr(
                        self.trigger_manager.get_trigger(trigger_id), "ui_name", ""
                    ),
                    "event_type": event.type,
                    "channel": channel,
                    "sender": sender,
                    "content": raw_content[:2000] if raw_content else "",
                },
            )

        self.trigger_manager.on_trigger_fired = _on_trigger_fired

    def _wire_completion_callbacks(self) -> None:
        """Wire executor and sub-agent completion/activity callbacks."""
        # Background tool completions are delivered through the executor's
        # _on_complete callback. Sub-agent completions flow through the
        # BackgroundifyHandle wrapper instead — see
        # ``modules/subagent/manager.py._run_subagent`` and
        # ``core/agent/tools/dispatch.py._on_backgroundify_complete``.
        self.executor._on_complete = self._on_bg_complete

        # Wire sub-agent tool activity -> parent output
        def _on_sa_tool_activity(
            sa_name, activity_type, tool_name, detail, sa_job_id="", extra=None
        ):
            meta = {
                "subagent": sa_name,
                "tool": tool_name,
                "detail": detail,
                "job_id": sa_job_id,
            }
            if extra:
                meta.update(extra)
            self.output_router.notify_activity(
                f"subagent_{activity_type}",
                f"[{sa_name}] [{tool_name}] {detail}",
                metadata=meta,
            )

        self.subagent_manager._on_tool_activity = _on_sa_tool_activity

    async def _init_mcp(self) -> None:
        await init_mcp(self)

    def _inject_mcp_tools_into_prompt(self) -> None:
        inject_mcp_tools_into_prompt(self)

    def _init_compact_manager(self) -> None:
        """Initialize the auto-compact manager.

        If compact.max_tokens not set, derives from LLM profile's max_context.
        Restores compact_count from session store if available.
        """
        compact_data = self.config.compact or {}
        default_compact_max = CompactConfig.max_tokens
        if hasattr(self.llm, "_profile_max_context"):
            default_compact_max = self.llm._profile_max_context
        compact_cfg = CompactConfig(
            max_tokens=compact_data.get("max_tokens", default_compact_max),
            threshold=compact_data.get("threshold", CompactConfig.threshold),
            target=compact_data.get("target", CompactConfig.target),
            keep_recent_turns=compact_data.get(
                "keep_recent_turns", CompactConfig.keep_recent_turns
            ),
            enabled=compact_data.get("enabled", CompactConfig.enabled),
            cooldown_seconds=compact_data.get(
                "cooldown_seconds", CompactConfig.cooldown_seconds
            ),
            compact_model=compact_data.get("compact_model"),
        )
        self.compact_manager = CompactManager(compact_cfg)
        self.compact_manager._controller = self.controller
        self.compact_manager._agent = self
        self.compact_manager._llm = self._build_compact_llm(compact_cfg)
        self.compact_manager._output_router = self.output_router
        self.compact_manager._agent_name = self.config.name
        self._wire_overflow_rescue()
        if self.session_store:
            self.compact_manager._session_store = self.session_store
            restore_compact_state_from_session(
                self.compact_manager, self.session_store, self.config.name
            )
        else:
            restore_resume_compact_state(self.compact_manager, None, self.config.name)

    def _init_plugins(self) -> None:
        """Initialize plugins from config + discover from packages."""
        if self.plugins:
            if hasattr(self, "controller"):
                self.controller.plugins = self.plugins
            if self.compact_manager is not None:
                self.compact_manager._plugins = self.plugins
            if self._termination_checker is not None:
                self._termination_checker.attach_plugins(self.plugins)
                self._termination_checker.attach_scratchpad(
                    getattr(self, "scratchpad", None)
                )
            if hasattr(self, "subagent_manager") and self.subagent_manager is not None:
                self.subagent_manager._parent_plugins = self.plugins
            if hasattr(self, "controller"):
                self._apply_plugin_hooks()
            track_plugin_selection(self)
            return
        plugin_cfgs = getattr(self.config, "plugins", []) or []
        self.plugins = init_plugins(
            plugin_cfgs,
            self._loader,
            default_plugins=getattr(self.config, "default_plugins", []) or [],
            strict=self._strict,
        )
        if not self.plugins:
            return
        track_plugin_selection(self)
        if hasattr(self, "controller"):
            self.controller.plugins = self.plugins
        # Compact manager uses on_compact_start as a veto point + on_compact_end callback.
        if self.compact_manager is not None:
            self.compact_manager._plugins = self.plugins
        # Plugin-supplied termination checkers (cluster 3.2/3.3).
        if self._termination_checker is not None:
            self._termination_checker.attach_plugins(self.plugins)
            self._termination_checker.attach_scratchpad(
                getattr(self, "scratchpad", None)
            )
        # Sub-agent manager fires parent's ``post_subagent_run`` hook
        # at result collection — give it a reference to our plugins.
        if hasattr(self, "subagent_manager") and self.subagent_manager is not None:
            self.subagent_manager._parent_plugins = self.plugins
        if hasattr(self, "controller"):
            self._apply_plugin_hooks()

    async def _load_plugins(self) -> None:
        """Load plugins, register pluggable commands, fire on_agent_start."""
        if not self.plugins:
            return
        wd = Path(self.executor._working_dir) if self.executor else default_workdir()
        ctx = PluginContext(
            agent_name=self.config.name,
            working_dir=wd,
            model=getattr(self.llm, "model", ""),
            _host_agent=self,
            _spawn_child_agent_helper=spawn_child_agent,
        )
        await self.plugins.load_all(ctx)
        # Pluggable ##xxx## commands (cluster C.1) — after on_load so
        # plugins can lazy-build their command handlers.
        register_plugin_and_package_commands(self)
        await self.plugins.notify("on_agent_start")

    def _apply_plugin_hooks(self) -> None:
        """No-op stub kept for backward compat.

        Plugin hooks fire at the call site — the executor invokes
        ``pre_tool_execute`` / ``post_tool_execute`` per tool call (see
        ``core/executor.py``) and the dispatch layer fires
        ``pre_subagent_run`` (see ``core/agent/turn/pre_dispatch.py``).
        We must NOT mutate ``tool.execute`` here: tool instances are
        shared between the parent's registry and every sub-agent's
        ``parent_registry`` reference, so a per-agent rebind would leak
        the wrapping plugin chain across agents — sub-agent budget
        plugins would then block the parent's tool calls. Keeping this
        method empty means each agent's plugins fire only when that
        agent dispatches the tool itself.
        """
        return None
