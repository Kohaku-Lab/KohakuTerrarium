"""
Agent - Main orchestrator that wires all components together.

The Agent class is the top-level entry point for running an agent.
It manages the lifecycle of all modules and the main event loop.

Component initialization is in agent_init.py (AgentInitMixin).
Event handling and tool execution is in agent_handlers.py (AgentHandlersMixin).
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.agent_model import AgentModelMixin
from kohakuterrarium.core.agent_observability import (
    build_session_info as _build_session_info,
    init_branch_state,
    wire_plugin_hook_timing,
    wire_scratchpad_observer,
)
from kohakuterrarium.core.budget import IterationBudget
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.config import AgentConfig, load_agent_config
from kohakuterrarium.core.controller_plugins import register_plugin_and_package_commands
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.job import JobState
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.termination import TerminationChecker, TerminationConfig
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.llm.message import ContentPart
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.plugins_context import spawn_child_agent
from kohakuterrarium.session.agent_attach import attach_to_session as _attach_to_session
from kohakuterrarium.session.agent_attach import (
    detach_from_session as _detach_from_session,
)
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.environment import Environment

logger = get_logger(__name__)


class Agent(AgentInitMixin, AgentHandlersMixin, AgentMessagesMixin, AgentModelMixin):
    """Main agent orchestrator. Wires LLM, controller, executor, I/O."""

    @classmethod
    def from_path(
        cls,
        config_path: str,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: Optional["Environment"] = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ) -> "Agent":
        """
        Create agent from config directory path.

        Args:
            config_path: Path to agent config folder (e.g., "agents/my_agent")
            input_module: Custom input module (overrides config)
            output_module: Custom output module (overrides config)
            session: Explicit session (creature-private state)
            environment: Shared environment (inter-creature state)
            llm_override: Override LLM profile name (from --llm CLI flag)
            pwd: Explicit working directory (overrides process cwd)

        Returns:
            Configured Agent instance
        """
        config = load_agent_config(config_path)
        return cls(
            config,
            input_module=input_module,
            output_module=output_module,
            session=session,
            environment=environment,
            llm_override=llm_override,
            pwd=pwd,
        )

    def __init__(
        self,
        config: AgentConfig,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: Optional["Environment"] = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ):
        """
        Initialize agent from config.

        Args:
            config: Agent configuration
            input_module: Custom input module (uses config if None)
            output_module: Custom output module (uses config if None)
            session: Explicit session (creature-private state). Created from
                     session_key if not provided.
            environment: Shared environment (inter-creature state). None for
            llm_override: Override LLM profile name (from --llm CLI flag)
                         standalone agents.
        """
        self.config = config
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._processing_lock = asyncio.Lock()
        self.trigger_manager = TriggerManager(self._process_event)

        # LLM profile override (from --llm CLI flag)
        self._llm_override = llm_override
        # Canonical ``provider/name[@variations]`` identifier for the
        # currently-bound profile, populated lazily by ``llm_identifier()``
        # and refreshed on every ``switch_model()`` call. Used by the
        # rich-CLI banner, ``/model`` output, session_info metadata, and
        # the web ModelSwitcher pill so every surface shows the same form.
        self._llm_identifier: str = ""

        # Explicit working directory (from web API pwd field)
        self._explicit_pwd = pwd

        # Session persistence (set externally via attach_session_store)
        self.session_store: Any = None
        self._session_output: Any = None
        self._pending_resume_events: list[dict] | None = None

        # Interrupt: flag + task reference for immediate cancellation
        self._interrupt_requested = False
        self._processing_task: asyncio.Task | None = None

        self._active_handles: dict[str, Any] = {}
        self._direct_job_meta: dict[str, dict[str, Any]] = {}
        self._bg_controller_notify: dict[str, bool] = {}

        self.compact_manager: Any = None
        self.plugins: Any = None  # PluginManager | None

        init_branch_state(self)

        # Environment and session (explicit or auto-created in _init_executor)
        self.environment: Optional["Environment"] = environment
        self._explicit_session: Session | None = session

        # Module loader for custom components
        self._loader = ModuleLoader(agent_path=config.agent_path)

        # Initialize termination checker
        self._termination_checker = self._init_termination()

        # Initialize components (methods from AgentInitMixin)
        # Order matters: output before controller (need known_outputs for parser)
        self._init_llm()
        self._init_registry()
        self._init_executor()
        self._init_subagents()
        self._init_iteration_budget()
        self._init_output(output_module)  # Before controller - sets _known_outputs
        self._init_skills()  # Before controller so skill index is in prompt
        self._init_controller()
        self._init_input(input_module)
        self._init_user_commands()
        self._init_triggers()

        logger.info(
            "Agent initialized",
            agent_name=config.name,
            model=getattr(self.llm, "model", config.model),
            tools=len(self.registry.list_tools()),
            triggers=len(self.trigger_manager.list()),
            ephemeral=config.ephemeral,
        )

    def _init_iteration_budget(self) -> None:
        """Create shared IterationBudget; parent + children consume it.

        Sub-agents that inherit share this counter. The parent
        controller also consumes one slot per turn in
        ``AgentHandlersMixin._check_termination`` — see Cluster 6.1 in
        ``plans/harness/extension-point-decisions.md``.
        """
        cap = getattr(self.config, "max_iterations", None)
        if not cap or cap <= 0:
            self.iteration_budget = None
            return
        self.iteration_budget = IterationBudget(remaining=int(cap), total=int(cap))
        if hasattr(self, "subagent_manager") and self.subagent_manager is not None:
            self.subagent_manager.iteration_budget = self.iteration_budget
        logger.info(
            "Iteration budget configured",
            agent_name=self.config.name,
            max_iterations=cap,
        )

    def _init_termination(self) -> TerminationChecker | None:
        """Initialize termination checker from config."""
        if not self.config.termination:
            return None

        tc = TerminationConfig(
            max_turns=self.config.termination.get("max_turns", 0),
            max_tokens=self.config.termination.get("max_tokens", 0),
            max_duration=self.config.termination.get("max_duration", 0),
            idle_timeout=self.config.termination.get("idle_timeout", 0),
            keywords=self.config.termination.get("keywords", []),
        )
        checker = TerminationChecker(tc)
        if checker.is_active:
            logger.info("Termination conditions configured", config=str(tc))
        return checker

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        """Start all agent modules."""
        logger.info("Starting agent", agent_name=self.config.name)

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
        self._shutdown_event.clear()

        # Initialize MCP client manager if mcp_servers configured
        await self._init_mcp()
        self._inject_mcp_tools_into_prompt()

        self._init_compact_manager()
        self._init_plugins()
        await self._load_plugins()
        self._publish_session_info()

        if self._termination_checker:
            self._termination_checker.start()

    def _configure_tui_tabs(self) -> None:
        """Configure TUI with terrarium tabs if available (set by runtime).

        The terrarium runtime stores tab/runtime info on session.extra
        before calling agent.run(). This method is a no-op hook that
        confirms the data is already in place for TUIInput to read.
        """
        # Data is written to session.extra by TerrariumRuntime.run()
        # before agent.run() -> agent.start() -> here, so nothing
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
        # ``core/agent_tools.py._on_backgroundify_complete``.
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
        """Initialize MCP client manager and connect configured servers."""
        mcp_configs = self.config.mcp_servers
        if not mcp_configs:
            self._mcp_manager = None
            return

        try:
            from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig
        except ImportError:
            logger.warning("MCP configured but mcp package not installed")
            self._mcp_manager = None
            return

        self._mcp_manager = MCPClientManager()

        for srv_data in mcp_configs:
            if not isinstance(srv_data, dict):
                continue
            try:
                config = MCPServerConfig(
                    name=srv_data.get("name", ""),
                    transport=srv_data.get("transport", "stdio"),
                    command=srv_data.get("command", ""),
                    args=srv_data.get("args", []),
                    env=srv_data.get("env", {}),
                    url=srv_data.get("url", ""),
                )
                if config.name:
                    await self._mcp_manager.connect(config)
            except Exception as e:
                logger.warning(
                    "Failed to connect MCP server",
                    server=srv_data.get("name", ""),
                    error=str(e),
                )

    def _inject_mcp_tools_into_prompt(self) -> None:
        """Inject available MCP tool descriptions into the system prompt.

        After MCP servers connect, the agent should know what tools are
        available without needing to call mcp_list first.  We append the
        tool listing to the system prompt so the agent can use mcp_call
        directly.
        """
        if not self._mcp_manager:
            return
        servers = self._mcp_manager.list_servers()
        if not servers:
            return

        lines = ["\n## Available MCP Tools\n"]
        lines.append(
            "Call these with: mcp_call(server=<server>, tool=<tool>, args={...})\n"
        )

        for srv in servers:
            if srv["status"] != "connected":
                continue
            lines.append(f"### Server: {srv['name']}")
            for t in srv["tools"]:
                desc = f" — {t['description']}" if t.get("description") else ""
                lines.append(f"- **{t['name']}**{desc}")
                schema = t.get("input_schema", {})
                props = schema.get("properties", {})
                required = set(schema.get("required", []))
                for pname, pinfo in props.items():
                    ptype = pinfo.get("type", "any")
                    pdesc = pinfo.get("description", "")
                    req = " (required)" if pname in required else ""
                    param_line = f"  - `{pname}`: {ptype}{req}"
                    if pdesc:
                        param_line += f" — {pdesc}"
                    lines.append(param_line)
            lines.append("")

        if len(lines) <= 2:
            return

        mcp_section = "\n".join(lines)
        self.update_system_prompt(mcp_section)
        logger.info(
            "MCP tools injected into prompt",
            servers=len(servers),
            tools=sum(len(s["tools"]) for s in servers),
        )

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
        )
        self.compact_manager = CompactManager(compact_cfg)
        self.compact_manager._controller = self.controller
        self.compact_manager._llm = self.llm
        self.compact_manager._output_router = self.output_router
        self.compact_manager._agent_name = self.config.name
        if self.session_store:
            self.compact_manager._session_store = self.session_store
            # Restore compact_count from session so round numbering continues
            try:
                saved_count = self.session_store.state.get(
                    f"{self.config.name}:compact_count"
                )
                if saved_count is not None:
                    self.compact_manager._compact_count = int(saved_count)
                    logger.info(
                        "Compact count restored",
                        compact_count=self.compact_manager._compact_count,
                    )
            except (KeyError, TypeError, ValueError):
                pass

    def _init_plugins(self) -> None:
        """Initialize plugins from config + discover from packages."""
        plugin_cfgs = getattr(self.config, "plugins", []) or []
        self.plugins = init_plugins(plugin_cfgs, self._loader)
        if not self.plugins:
            return
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
        self._apply_plugin_hooks()

    async def _load_plugins(self) -> None:
        """Load plugins, register pluggable commands, fire on_agent_start."""
        if not self.plugins:
            return
        wd = Path(self.executor._working_dir) if self.executor else Path.cwd()
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
        """Wrap methods with plugin pre/post hooks (transparent decoration)."""
        pm = self.plugins
        for tool_name in self.registry.list_tools():
            tool = self.registry.get_tool(tool_name)
            if tool and hasattr(tool, "execute"):
                tool.execute = pm.wrap_method(
                    "pre_tool_execute",
                    "post_tool_execute",
                    tool.execute,
                    input_kwarg="args",
                    extra_kwargs={"tool_name": tool_name},
                )
        self.subagent_manager._run_subagent = pm.wrap_method(
            "pre_subagent_run",
            "post_subagent_run",
            self.subagent_manager._run_subagent,
        )

    def _publish_session_info(self) -> None:
        """Publish session info to output (for TUI session panel).

        Handles session ID retrieval, LLM profile persistence,
        prompt cache key, embedding config, and session_info notification.
        """
        session_id = ""
        if self.session_store:
            try:
                meta = self.session_store.load_meta()
                session_id = meta.get("session_id", "")
            except Exception as e:
                logger.debug(
                    "Failed to load session meta for session_id",
                    error=str(e),
                    exc_info=True,
                )

        # Save selected preset/profile name to session state for resume
        selected_llm_name = self._llm_override or self.config.llm_profile or ""
        if self.session_store and selected_llm_name:
            self.session_store.state[f"{self.config.name}:llm_profile"] = (
                selected_llm_name
            )

        # Set prompt_cache_key on LLM provider for cache routing
        if session_id and hasattr(self.llm, "prompt_cache_key"):
            self.llm.prompt_cache_key = session_id
            logger.info("Prompt cache key set", cache_key=session_id[:16])

        # Save embedding config to session state for search_memory tool and resume
        if self.session_store:
            memory_cfg = getattr(self.config, "memory", None)
            embed_cfg = (
                memory_cfg.get("embedding") if isinstance(memory_cfg, dict) else None
            )
            if embed_cfg:
                self.session_store.state["embedding_config"] = embed_cfg

        # Get the actual model name from the LLM provider
        model = (
            getattr(self.llm, "model", "")
            or getattr(getattr(self.llm, "config", None), "model", "")
            or "(no model - backend)"
        )
        compact_cfg = self.compact_manager.config
        max_context = compact_cfg.max_tokens
        compact_at = int(max_context * compact_cfg.threshold) if max_context else 0
        # ``llm_name`` carries the canonical ``provider/name[@variations]``
        # identifier so CLI banners, the web ModelSwitcher pill, and
        # future ``/model`` invocations all show the same selector form.
        # Falls back to whatever the user originally specified if
        # resolution failed (shouldn't happen at this point but we're
        # conservative — the event still fires with best-effort data).
        llm_identifier = self.llm_identifier() or selected_llm_name
        self.output_router.notify_activity(
            "session_info",
            "",
            metadata={
                "session_id": session_id,
                "model": model,
                "llm_name": llm_identifier,
                "agent_name": self.config.name,
                "max_context": max_context,
                "compact_threshold": compact_at,
            },
        )

    def interrupt(self) -> None:
        """Interrupt the current processing cycle immediately.

        Cancels the processing task directly, which propagates
        CancelledError through whatever is awaiting (LLM stream,
        tool gather, etc.). The agent stays alive for the next input.

        Only direct jobs owned by the active processing cycle are cancelled.
        Background jobs keep running.
        """
        self._interrupt_requested = True
        self.controller._interrupted = True

        # Cancel the processing task (immediate, not flag-based)
        processing = getattr(self, "_processing_task", None)
        if processing and not processing.done():
            processing.cancel()

        # Cancel only direct, non-promoted jobs from the active run.
        for job_id in list(self._active_handles.keys()):
            self._interrupt_direct_job(job_id)

        # Plugin callback (fire-and-forget, non-blocking)
        if self.plugins:
            asyncio.create_task(self.plugins.notify("on_interrupt"))

        logger.info("Agent interrupted", agent_name=self.config.name)

    def _cancel_job(self, job_id: str, job_name: str) -> None:
        """Cancel a single running job by ID (tool or sub-agent).

        Called from the TUI running panel click handler.
        """
        cancelled = self._interrupt_direct_job(job_id)

        if not cancelled:
            # Try executor (tools) first
            task = self.executor._tasks.get(job_id)
            if task and not task.done():
                task.cancel()
                self.executor.job_store.update_status(job_id, state=JobState.CANCELLED)
                cancelled = True

            # Try sub-agent manager
            if not cancelled:
                sa_task = self.subagent_manager._tasks.get(job_id)
                if sa_task and not sa_task.done():
                    sa_task.cancel()
                    self.subagent_manager.job_store.update_status(
                        job_id, state=JobState.CANCELLED
                    )
                    cancelled = True

        if cancelled:
            logger.info(
                "Job cancelled via TUI",
                job_id=job_id,
                job_name=job_name,
                agent_name=self.config.name,
            )
            # Notify output so the running panel updates
            self.output_router.notify_activity(
                "job_cancelled",
                f"Cancelled: {job_name}",
                metadata={"job_id": job_id, "job_name": job_name},
            )

    def _promote_handle(self, job_id: str) -> bool:
        """Promote a direct task to background. Thread-safe (TUI + API)."""
        handle = self._active_handles.get(job_id)
        if not handle:
            return False

        # Thread-safe promotion: asyncio.Event.set() must run on the
        # event loop thread. TUI calls this from Textual's thread.
        try:
            loop = asyncio.get_running_loop()
            # Already on the event loop (API handler) — promote directly
            if not handle.promote():
                return False
        except RuntimeError:
            # Not on an event loop (TUI thread) — schedule on the agent's loop
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(handle.promote)
            except RuntimeError:
                return False

        self.output_router.notify_activity(
            "task_promoted",
            f"[{job_id}] Moved to background",
            metadata={"job_id": job_id},
        )
        logger.info("Task promoted via UI", job_id=job_id)
        return True

    # ``switch_model`` + ``llm_identifier`` live in AgentModelMixin —
    # see ``core/agent_model.py``. Split out to keep this file under
    # the per-file size guard.

    async def stop(self) -> None:
        """Stop all agent modules."""
        logger.info("Stopping agent", agent_name=self.config.name)

        if self.plugins:
            await self.plugins.notify("on_agent_stop")
            await self.plugins.unload_all()

        self._running = False
        self._shutdown_event.set()

        if hasattr(self, "_mcp_manager") and self._mcp_manager:
            await self._mcp_manager.shutdown()

        await self.subagent_manager.cancel_all()
        await self.trigger_manager.stop_all()
        await self.input.stop()
        await self.output_router.stop()
        await self.llm.close()

    async def run(self) -> None:
        """
        Run the agent main loop.

        Handles:
        - Startup triggers
        - Getting input
        - Running controller
        - Processing tool calls
        - Routing output
        """
        await self.start()

        try:
            # Replay session history to output if resuming
            if self._pending_resume_events:
                await self.output_router.on_resume(self._pending_resume_events)
                self._pending_resume_events = None

            # Re-create resumable triggers from saved state
            pending_triggers = getattr(self, "_pending_resume_triggers", None)
            if pending_triggers:
                await self._restore_triggers(pending_triggers)
                self._pending_resume_triggers = None

            # Fire startup trigger if configured
            await self._fire_startup_trigger()

            idle_logged = False
            while self._running:

                # Get input (triggers fire _process_event directly via separate tasks)
                if not idle_logged:
                    logger.debug("Agent idle, waiting for input...")
                    idle_logged = True

                event = await self.input.get_input()

                # Check for exit
                if event is None:
                    if (
                        hasattr(self.input, "exit_requested")
                        and self.input.exit_requested
                    ):
                        logger.info("Exit requested")
                        break
                    continue

                idle_logged = False
                # Log content length (handle multimodal)
                if event.is_multimodal():
                    content_len = len(event.get_text_content())
                    content_info = f"{content_len} chars + {len(event.content)} parts"
                else:
                    content_len = len(event.content) if event.content else 0
                    content_info = f"{content_len} chars"
                logger.info(
                    "Input received, processing event",
                    event_type=event.type,
                    content=content_info,
                )

                await self._process_event(event)
                logger.debug("Event processing complete, returning to idle")

        except KeyboardInterrupt:
            logger.info("Interrupted")
        except asyncio.CancelledError:
            logger.info("Agent cancelled")
        except Exception as e:
            logger.error("Fatal agent error", error=str(e))
            # Try to show error in output before stopping
            try:
                error_type = type(e).__name__
                await self.output_router.default_output.write(
                    f"\n[Fatal Error] {error_type}: {e}\n"
                )
                await self.output_router.on_processing_end()
            except Exception as e:
                logger.debug(
                    "Failed to write fatal error to output", error=str(e), exc_info=True
                )
            raise
        finally:
            await self.stop()

    # =========================================================================
    # Programmatic API
    # =========================================================================

    @property
    def is_running(self) -> bool:
        """Check if agent is running."""
        return self._running

    @property
    def tools(self) -> list[str]:
        """Get list of registered tool names."""
        return self.registry.list_tools()

    @property
    def subagents(self) -> list[str]:
        """Get list of registered sub-agent names."""
        return self.subagent_manager.list_subagents()

    @property
    def conversation_history(self) -> list[dict]:
        """Get conversation history as list of message dicts."""
        return self.controller.conversation.to_messages()

    async def inject_input(
        self, content: str | list[ContentPart], source: str = "programmatic"
    ) -> None:
        """Inject user input programmatically."""
        content = await self._prepare_injected_input(content, source)
        if content is None:
            return
        event = create_user_input_event(content, source=source)
        await self._process_event(event)

    async def inject_event(self, event: TriggerEvent) -> None:
        """Inject a custom TriggerEvent programmatically."""
        await self._process_event(event)

    def attach_session_store(
        self, store: Any, *, capture_activity: bool = True
    ) -> None:
        """Attach a SessionStore; registers a SessionOutput sink.

        Wires scratchpad + plugin-hook observers so Wave B
        ``scratchpad_write`` / ``plugin_hook_timing`` events flow.
        """
        self.session_store = store
        # Controller needs direct access for artifact writes.
        if hasattr(self, "controller") and self.controller is not None:
            self.controller.session_store = store
        self._session_output = SessionOutput(
            self.config.name, store, self, capture_activity=capture_activity
        )
        self.output_router.add_secondary(self._session_output)
        if hasattr(self, "subagent_manager"):
            self.subagent_manager._session_store = store
            self.subagent_manager._parent_name = self.config.name
        self.trigger_manager._session_store = store
        self.trigger_manager._agent_name = self.config.name
        # Terrariums attach the store AFTER agent.start() (creatures),
        # so re-read the saved compact_count or it resets to 0.
        if self.compact_manager:
            self.compact_manager._session_store = store
            try:
                saved = store.state.get(f"{self.config.name}:compact_count")
                if saved is not None:
                    self.compact_manager._compact_count = int(saved)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug(
                    "compact_count restore skipped",
                    agent=self.config.name,
                    error=str(e),
                )
        # Wave B: route scratchpad + plugin-hook events to the router.
        wire_scratchpad_observer(self)
        wire_plugin_hook_timing(self)
        logger.debug("Session store attached", agent=self.config.name)

    # Wave F attach / detach — implementation in
    # ``kohakuterrarium.session.attach``; thin wrappers here to keep
    # this file under the 1000-line hard cap.
    attach_to_session = _attach_to_session
    detach_from_session = _detach_from_session

    def set_output_handler(self, handler: Any, replace_default: bool = False) -> None:
        """Set a custom output handler callback for text chunks."""

        # Create a simple callback output module
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

    # =========================================================================
    # Hot-plug API
    # =========================================================================

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

    def update_system_prompt(self, content: str, replace: bool = False) -> None:
        """Update the system prompt of a running agent.

        Args:
            content: New content to append (or full replacement if replace=True)
            replace: If True, replace entire system prompt. If False, append.
        """
        sys_msg = self.controller.conversation.get_system_message()
        if sys_msg is None:
            return

        if replace:
            sys_msg.content = content
        else:
            if isinstance(sys_msg.content, str):
                sys_msg.content = sys_msg.content + "\n\n" + content

        logger.info("System prompt updated", replace=replace, added_length=len(content))

    def get_system_prompt(self) -> str:
        """Get the current system prompt text."""
        sys_msg = self.controller.conversation.get_system_message()
        if sys_msg and isinstance(sys_msg.content, str):
            return sys_msg.content
        return ""

    def get_state(self) -> dict[str, Any]:
        """Get agent state for monitoring."""
        return {
            "name": self.config.name,
            "running": self._running,
            "tools": self.tools,
            "subagents": self.subagents,
            "message_count": len(self.conversation_history),
            "pending_jobs": self.executor.get_pending_count() if self.executor else 0,
        }

    def session_info(self, tokens_view: str = "own") -> dict[str, Any]:
        """Wave G session snapshot (see ``build_session_info``)."""
        return _build_session_info(self, tokens_view)


async def run_agent(config_path: str) -> None:
    """
    Convenience function to run an agent from config path.

    Args:
        config_path: Path to agent config folder
    """
    config = load_agent_config(config_path)
    agent = Agent(config)
    await agent.run()
