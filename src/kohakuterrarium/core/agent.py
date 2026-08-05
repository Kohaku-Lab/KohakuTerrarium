"""
Top-level agent orchestration for component lifecycle and event processing.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.core.agent_compact import (
    AgentCompactMixin,
    restore_compact_state_from_session,
)
from kohakuterrarium.core.agent_construct import AgentConstructMixin
from kohakuterrarium.core.agent_event_loop import AgentEventLoopMixin
from kohakuterrarium.core.agent_extensions import AgentExtensionsMixin
from kohakuterrarium.core.agent_turn import AgentTurnMixin
from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
from kohakuterrarium.core.agent_lifecycle import AgentLifecycleMixin
from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.agent_mcp import init_mcp, inject_mcp_tools_into_prompt
from kohakuterrarium.core.agent_model import AgentModelMixin
from kohakuterrarium.core.agent_helpers import attach_session_helpers
from kohakuterrarium.core.agent_observability import (
    build_session_info as _build_session_info,
    init_branch_state,
    wire_plugin_hook_timing,
    wire_scratchpad_observer,
)
from kohakuterrarium.core.agent_budget_recovery import (
    sync_emergency_drop_conversation,
)
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.config import AgentConfig, load_agent_config
from kohakuterrarium.core.event_inbox import EventInbox, TurnOutcome
from kohakuterrarium.core.controller_plugins import register_plugin_and_package_commands
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.core.job import JobState
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.llm.message import ContentPart
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.plugins_context import spawn_child_agent
from kohakuterrarium.session.agent_attach import attach_to_session as _attach_to_session
from kohakuterrarium.session.agent_attach import (
    detach_from_session as _detach_from_session,
)
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir

if TYPE_CHECKING:
    from kohakuterrarium.core.environment import Environment

logger = get_logger(__name__)


class Agent(
    AgentConstructMixin,
    AgentTurnMixin,
    AgentExtensionsMixin,
    AgentInitMixin,
    AgentEventLoopMixin,
    AgentHandlersMixin,
    AgentMessagesMixin,
    AgentModelMixin,
    AgentCompactMixin,
    AgentLifecycleMixin,
):
    """Main agent orchestrator. Wires LLM, controller, executor, I/O.

    Construction entry points (``Agent.build`` / ``Agent.from_path``)
    live in :class:`AgentConstructMixin`; the typed turn drivers
    (``run`` / ``run_stream``) in :class:`AgentTurnMixin`; runtime
    extension injection (``add_tool`` / ``add_plugin``) in
    :class:`AgentExtensionsMixin`.
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        input_module: InputModule | None = None,
        output_module: OutputModule | None = None,
        session: Session | None = None,
        environment: "Environment | None" = None,
        llm: Any = None,
        pwd: str | None = None,
        strict: bool = True,
        tools: list[Any] | None = None,
        plugins: list[Any] | None = None,
        subagents: list[Any] | None = None,
        outputs: dict[str, OutputModule] | None = None,
        user_commands: dict[str, Any] | None = None,
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
                         standalone agents.
            llm: LLM binding — provider instance, selector string,
                 ``LLMProfile``, or None (resolve from config)
            pwd: Explicit working directory (overrides process cwd)
            strict: Raise on construction problems (unresolvable LLM,
                    unknown tool, broken plugin) instead of degrading
                    silently.  Interactive frontends (Studio / Lab /
                    ``kt run``) pass ``strict=False`` so a user can fix
                    the problem at runtime (e.g. rebind the model);
                    programmatic callers get loud failures by default.
            tools: Tool INSTANCES to register alongside the config's
                   tools (e.g. ``kt.tool(fn)`` adapters) — present in
                   the initial system prompt.
            plugins: Plugin INSTANCES to register (enabled).
            subagents: ``SubAgentConfig`` instances to register.
            outputs: Extra named outputs ``{name: OutputModule}``.
            user_commands: Extra slash commands ``{name: UserCommand}``.
        """
        self.config = config
        self._strict = strict
        self._running = False
        # Warm-pause admission gate (UXI-11): when True, new turns are
        # buffered instead of run; the runtime stays live.
        self._paused = False
        self._shutdown_event = asyncio.Event()
        # Set once the startup trigger's awaited turn has settled inside
        # ``_drive_input`` (or immediately when no startup trigger is
        # configured). The Terrarium restoration barrier waits on this so
        # Drive reconciliation never runs against an un-settled creature.
        self._startup_settled = asyncio.Event()
        # Turn mutex — now held ONLY by the single event consumer for the
        # span of a turn so ``stop()`` can join an in-flight turn.
        self._processing_lock = asyncio.Lock()
        self._turn_lock_holder: asyncio.Task | None = None
        # False while a NON-stackable turn (startup / error / Drive) holds
        # the mutex, so nothing folds into it.
        self._active_turn_stackable = True
        # The single infinite event queue + its one consumer (queue-first
        # event system — see ``core/event_inbox.py`` + ``agent_event_loop.py``).
        self._event_inbox = EventInbox()
        self._consumer_task: asyncio.Task | None = None
        # Warm-pause gate for the consumer: set = draining, cleared = paused.
        self._consumer_resume = asyncio.Event()
        self._consumer_resume.set()
        # A trigger's drained backlog stashed by ``admit_ready_events`` and
        # flushed onto the inbox right after its primary (UXI-08b).
        self._trigger_backlog_stash: list[TriggerEvent] = []
        self._bg_dispatch_acks: list[str] = []
        self.trigger_manager = TriggerManager(self._process_event)
        # A trigger drains its whole ready backlog on fire and admits the
        # extras here so the burst is absorbed in ONE turn (UXI-08b).
        self.trigger_manager.admit_ready = self.admit_ready_events

        # Raw llm= argument (instance | selector string | profile | None).
        self._llm_spec = llm
        # Selector string, when one was given — feeds llm_identifier()
        # and compact-LLM resolution.  Instance injections leave it None.
        self._llm_selector = llm if isinstance(llm, str) else None
        # Canonical provider/name[@variations] id; lazy via llm_identifier().
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
        self._branch_request_id: str | None = None

        self._active_handles: dict[str, Any] = {}
        self._direct_job_meta: dict[str, dict[str, Any]] = {}
        self._bg_controller_notify: dict[str, bool] = {}
        # Deliverable background jobs (notify=True, non-stateful/-interactive)
        # this turn spawned; the output-wiring guard defers only on these.
        self._turn_dispatched_bg: set[str] = set()

        self.compact_manager: Any = None
        self.plugins: Any = None  # PluginManager | None
        attach_session_helpers(self)

        init_branch_state(self)

        # Environment and session (explicit or auto-created in _init_executor)
        self.environment: "Environment | None" = environment
        self._explicit_session: Session | None = session

        # Module loader for custom components
        self._loader = ModuleLoader(agent_path=config.agent_path)

        # Initialize termination checker
        self._termination_checker = self._init_termination()

        # Extra slash commands merged in by _init_user_commands.
        self._extra_user_commands = dict(user_commands or {})

        # Initialize components (methods from AgentInitMixin)
        # Order matters: output before controller (need known_outputs for parser)
        self._init_llm()
        if hasattr(self.llm, "on_emergency_drop"):
            self.llm.on_emergency_drop(self._on_provider_emergency_drop)
        self._init_registry()
        self._init_executor()
        # Instance-injected tools (E7) — registered BEFORE the
        # controller so they land in the initial system prompt.
        for tool_instance in tools or []:
            self.registry.register_tool(tool_instance)
            self.executor.register_tool(tool_instance)
        self._init_plugins()
        for plugin_instance in plugins or []:
            self.plugins.register(plugin_instance)
        self._init_subagents()
        for subagent_cfg in subagents or []:
            self.subagent_manager.register(subagent_cfg)
        self._init_iteration_budget()
        self._init_output(output_module)  # Before controller - sets _known_outputs
        # Instance-injected named outputs join before the parser learns
        # the known-output set.
        for output_name, extra_output in (outputs or {}).items():
            self.output_router.named_outputs[output_name] = extra_output
            self._known_outputs.add(output_name)
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

    def _on_provider_emergency_drop(self, messages: list[dict[str, Any]]) -> None:
        """Synchronize controller conversation after provider emergency drop."""
        sync_emergency_drop_conversation(self, messages)

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

        # Spawn the single event consumer — the only reader of the inbox.
        # It must be running before the startup trigger fires (it drives
        # every turn now, including startup / user input / completions).
        self._consumer_resume.set()
        self._consumer_task = asyncio.create_task(
            self._run_event_consumer(), name=f"event_consumer_{self.config.name}"
        )

        # Initialize MCP client manager if mcp_servers configured
        await self._init_mcp()
        self._inject_mcp_tools_into_prompt()

        self._init_compact_manager()
        if self.plugins and self.compact_manager is not None:
            self.compact_manager._plugins = self.plugins
        await self._load_plugins()
        self._publish_session_info()

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
            keep_recent_turns=compact_data.get(
                "keep_recent_turns", CompactConfig.keep_recent_turns
            ),
            cooldown_seconds=compact_data.get(
                "cooldown_seconds", CompactConfig.cooldown_seconds
            ),
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
        ``pre_subagent_run`` (see ``core/agent_pre_dispatch.py``).
        We must NOT mutate ``tool.execute`` here: tool instances are
        shared between the parent's registry and every sub-agent's
        ``parent_registry`` reference, so a per-agent rebind would leak
        the wrapping plugin chain across agents — sub-agent budget
        plugins would then block the parent's tool calls. Keeping this
        method empty means each agent's plugins fire only when that
        agent dispatches the tool itself.
        """
        return None

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
                logger.warning(
                    "Failed to load session meta for session_id",
                    error=str(e),
                    exc_info=True,
                )

        # Save selected preset/profile name to session state for resume
        selected_llm_name = self._llm_selector or self.config.llm_profile or ""
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
        """Cancel the active turn; background jobs untouched.

        The consumer's turn body swallows the cancellation, finalizes, and
        loops — so any events still queued on the inbox process as the next
        fresh turn (no explicit re-fire needed). Queued Drive deliveries
        are dropped WITHOUT running and their settlement resolved
        ``interrupted`` so the dispatcher redelivers, matching the prior
        drop-drive-on-interrupt behavior."""
        self._interrupt_requested = True
        self.controller._interrupted = True
        processing = getattr(self, "_processing_task", None)
        if processing and not processing.done():
            processing.cancel()
        for job_id in list(self._active_handles.keys()):
            self._interrupt_direct_job(job_id)
        if self.plugins:
            asyncio.create_task(self.plugins.notify("on_interrupt"))
        dropped = self._event_inbox.remove_where(
            lambda ev: ev.type.startswith("drive_")
        )
        for env in dropped:
            fut = env.future
            if fut is not None and not fut.done():
                fut.set_result(TurnOutcome(status="interrupted", was_primary=False))
        if dropped:
            logger.info(
                "Dropped queued Drive deliveries after user interrupt",
                count=len(dropped),
            )
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

            # Try sub-agent manager. Use cooperative cancellation so
            # any tokens accumulated before cancellation are preserved in
            # the final SubAgentResult and session trace.
            if not cancelled:
                sa_task = self.subagent_manager._tasks.get(job_id)
                if sa_task and not sa_task.done():
                    job = self.subagent_manager._jobs.get(job_id)
                    if job and hasattr(job, "subagent"):
                        job.subagent.cancel()
                    sa_task.cancel()
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
            asyncio.get_running_loop()
            # Already on the event loop (API handler) — promote directly
            if not handle.promote():
                return False
        except RuntimeError:
            # TUI thread — schedule on the loop captured at ``start()``.
            # ``asyncio.get_event_loop()`` is unsafe on Python 3.14+.
            loop = getattr(self, "_loop", None)
            if loop is None or loop.is_closed():
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    return False
            loop.call_soon_threadsafe(handle.promote)

        self.output_router.notify_activity(
            "task_promoted",
            f"[{job_id}] Moved to background",
            metadata={"job_id": job_id},
        )
        logger.info("Task promoted via UI", job_id=job_id)
        return True

    async def run_forever(self) -> None:
        """Run the agent lifecycle and input loop until input exits."""
        await self.start()
        try:
            await self._drive_input()
        finally:
            await self.stop()

    async def _drive_input(self) -> None:
        """Replay resume state, fire the startup trigger, then poll input.

        Extracted from :meth:`run` so engine-managed creatures can spawn
        this as a background task in ``Creature.start`` — that's the
        path headless configured-IO agents (Discord bot, webhook
        listener, custom polling input) depend on. ``run`` keeps the
        non-engine standalone path; both routes share this body.
        """
        try:
            # Replay session history to output if resuming
            if self._pending_resume_events:
                await self.output_router.emit(
                    OutputEvent(
                        type="resume_batch",
                        payload={"events": self._pending_resume_events},
                    )
                )
                self._pending_resume_events = None

            # Re-create resumable triggers from saved state
            pending_triggers = getattr(self, "_pending_resume_triggers", None)
            if pending_triggers:
                await self._restore_triggers(pending_triggers)
                self._pending_resume_triggers = None

            # Fire startup trigger if configured
            await self._fire_startup_trigger()
            # Startup turn (if any) has now settled — release the
            # restoration barrier so engine-side Drive reconciliation runs.
            self._startup_settled.set()

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
                    await asyncio.sleep(0.01)
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
            raise
        except Exception as e:
            logger.error("Fatal agent error", error=str(e))
            # Try to show error in output before stopping
            try:
                error_type = type(e).__name__
                await self.output_router.default_output.write(
                    f"\n[Fatal Error] {error_type}: {e}\n"
                )
                await self.output_router.emit(OutputEvent(type="processing_end"))
            except Exception as inner:
                logger.warning(
                    "Failed to write fatal error to output",
                    error=str(inner),
                    exc_info=True,
                )
            raise

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
        self,
        content: str | list[ContentPart],
        source: str = "programmatic",
        pending_id: str | None = None,
    ) -> bool:
        """Inject user input programmatically.

        Returns True when the event ran; False when buffered for
        mid-turn drain — callers (WS attach) skip the post-call
        ``idle`` frame on False so KohakUwUing stays visible.

        ``pending_id`` lets a shell mint the queued-message id up front
        (UXI-08a) so it can target the message with ``edit_pending`` /
        ``cancel_pending`` the moment the queued ack lands. Ignored when
        the input runs immediately (it was never queued).
        """
        content = await self._prepare_injected_input(content, source)
        if content is None:
            # Slash-command or filter consumed the input — treat the
            # same as "processed" so the caller still gets its idle.
            return True
        event = create_user_input_event(content, source=source)
        if pending_id:
            event.context["pending_id"] = pending_id
        return await self._process_event(event)

    async def inject_event(self, event: TriggerEvent) -> bool:
        """Inject a custom TriggerEvent programmatically.

        Same return contract as :meth:`inject_input`."""
        return await self._process_event(event)

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
        if (
            self._session_output is None
            or getattr(self._session_output, "_store", None) is not store
        ):
            if self._session_output is not None:
                self.output_router.remove_secondary(self._session_output)
            self._session_output = SessionOutput(
                self.config.name, store, self, capture_activity=capture_activity
            )
            self.output_router.add_secondary(self._session_output)
        else:
            self._session_output._capture_activity = capture_activity
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
                logger.warning(
                    "compact_count restore skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        native_tool_options = getattr(self, "native_tool_options", None)
        if native_tool_options is not None:
            try:
                native_tool_options.apply()
            except Exception as e:
                logger.warning(
                    "native tool option apply skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        plugin_options = getattr(self, "plugin_options", None)
        if plugin_options is not None:
            try:
                plugin_options.apply()
            except Exception as e:
                logger.warning(
                    "plugin options apply skipped",
                    agent=self.config.name,
                    error=str(e),
                    exc_info=True,
                )
        # Wave B: route scratchpad + plugin-hook events to the router.
        wire_scratchpad_observer(self)
        wire_plugin_hook_timing(self)
        logger.debug("Session store attached", agent=self.config.name)

    # Aliases preserve the Agent API while session attachment remains centralized.
    attach_to_session = _attach_to_session
    detach_from_session = _detach_from_session

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
        """Build a session snapshot with the requested token accounting view."""
        return _build_session_info(self, tokens_view)


async def run_agent(config_path: str) -> None:
    """
    Convenience function to run an agent from config path.

    Args:
        config_path: Path to agent config folder
    """
    config = load_agent_config(config_path)
    agent = Agent(config)
    await agent.run_forever()
