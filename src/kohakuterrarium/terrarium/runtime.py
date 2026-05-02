"""
Terrarium runtime - multi-agent orchestration.

Creates channels, wires triggers, manages lifecycle.
Not an agent -- pure wiring.
"""

import asyncio
from typing import Any
from uuid import uuid4

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.terrarium.api import TerrariumAPI
from kohakuterrarium.terrarium.config import TerrariumConfig
from kohakuterrarium.terrarium.creature import CreatureHandle
from kohakuterrarium.terrarium.factory import build_creature, build_root_agent
from kohakuterrarium.terrarium.hotplug import HotPlugMixin
from kohakuterrarium.terrarium.observer import ChannelObserver
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.terrarium.persistence import attach_session_store
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TerrariumRuntime(HotPlugMixin):
    """
    Multi-agent orchestration runtime.

    Loads creature configs, creates channels, wires triggers,
    and manages lifecycle.  No intelligence -- pure wiring.

    Hot-plug methods (add_creature, remove_creature, add_channel,
    wire_channel) are provided by HotPlugMixin.
    """

    def __init__(
        self,
        config: TerrariumConfig,
        *,
        environment: Environment | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
    ):
        self.config = config
        self.llm_override = llm_override
        self.pwd = pwd
        # Use provided environment or create one
        self.environment = environment or Environment(
            env_id=f"terrarium_{config.name}_{uuid4().hex[:8]}"
        )
        self._creatures: dict[str, CreatureHandle] = {}
        self._session_key = self.environment.env_id  # for backward compat
        self._session: Session | None = None  # kept for backward compat
        self._running = False
        self._creature_tasks: list[asyncio.Task] = []
        self._root_agent: Agent | None = None
        self._pending_session_store: Any = None

    # ------------------------------------------------------------------
    # Lazy-initialized API / observer
    # ------------------------------------------------------------------

    @property
    def api(self) -> TerrariumAPI:
        """Get the programmatic API for this runtime."""
        if not hasattr(self, "_api"):
            self._api = TerrariumAPI(self)
        return self._api

    @property
    def observer(self) -> ChannelObserver:
        """Get the channel observer."""
        if not hasattr(self, "_observer"):
            if self._session is None:
                raise RuntimeError("Cannot create observer before terrarium is started")
            self._observer = ChannelObserver(self._session)
        return self._observer

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the terrarium.

        1. Pre-create shared channels in the environment.
        2. Create backward-compat session pointing at shared channels.
        3. For each creature:
           a. Load agent config.
           b. Create Agent with private session + shared environment.
           c. Inject ChannelTriggers for listen channels.
           d. Inject channel topology into system prompt.
        4. Start all creature agents.
        """
        if self._running:
            logger.warning("Terrarium already running")
            return

        logger.info("Starting terrarium", terrarium_name=self.config.name)

        # 1a. Pre-create shared channels from config
        for ch_cfg in self.config.channels:
            self.environment.shared_channels.get_or_create(
                ch_cfg.name,
                channel_type=ch_cfg.channel_type,
                description=ch_cfg.description,
            )
            logger.debug(
                "Channel created",
                channel=ch_cfg.name,
                channel_type=ch_cfg.channel_type,
            )

        # 1b. Auto-create a direct queue channel for each creature (named after it).
        # This lets the root agent or other creatures send messages directly
        # to a specific creature without extra config.
        for creature_cfg in self.config.creatures:
            self.environment.shared_channels.get_or_create(
                creature_cfg.name,
                channel_type="queue",
                description=f"Direct channel to {creature_cfg.name}",
            )
            logger.debug("Auto-created creature channel", channel=creature_cfg.name)

        # 1c. Auto-create "report_to_root" channel when root exists.
        # All creatures can send here; root auto-listens to all channels.
        if self.config.root:
            self.environment.shared_channels.get_or_create(
                "report_to_root",
                channel_type="queue",
                description="Any creature can report to the root agent",
            )
            logger.debug("Auto-created report_to_root channel")

        # 2. Backward-compat session - observer and API use _session.channels
        self._session = Session(key=self._session_key)
        self._session.channels = self.environment.shared_channels

        # 3. Build creatures
        for creature_cfg in self.config.creatures:
            handle = build_creature(
                creature_cfg,
                self.environment,
                self.config,
                llm_override=self.llm_override,
                pwd=self.pwd,
            )
            self._creatures[creature_cfg.name] = handle

        # 4. Start all creature agents
        for handle in self._creatures.values():
            await handle.agent.start()
            logger.info("Creature started", creature=handle.name)

        # 5. Build root agent if configured (OUTSIDE the terrarium)
        # Don't start it here - run() will call agent.run() which handles start
        if self.config.root:
            self._root_agent = build_root_agent(
                self.config,
                self.environment,
                self,
                llm_override=self.llm_override,
                pwd=self.pwd,
            )
            logger.info(
                "Root agent built",
                base_config=self.config.root.config_data.get("base_config"),
            )

        # 6. Install the output-wiring resolver on every agent.
        # Must happen AFTER all creatures + root exist so the resolver
        # can see every possible target at dispatch time.
        self._install_output_wiring_resolver()

        self._running = True
        logger.info(
            "Terrarium started",
            terrarium_name=self.config.name,
            creatures=len(self._creatures),
            has_root=self._root_agent is not None,
        )

    def _install_output_wiring_resolver(self) -> None:
        """Build and attach the output-wiring resolver on every agent.

        The resolver is shared across agents (it only holds references
        to creatures + root, both of which live for the terrarium's
        lifetime). Each agent's ``_wiring_resolver`` field is set by the
        core framework to ``None`` by default, which activates the
        NoopResolver behaviour. We replace it with the terrarium-aware
        resolver here.
        """
        _wiring.install_runtime_output_wiring_resolver(
            self._creatures,
            root_agent=self._root_agent,
        )
        logger.debug(
            "Output wiring resolver installed",
            creature_count=len(self._creatures),
            has_root=self._root_agent is not None,
        )

    async def stop(self) -> None:
        """Stop all creatures and clean up."""
        if not self._running:
            return

        logger.info("Stopping terrarium", terrarium_name=self.config.name)
        self._running = False

        # Cancel running creature tasks
        for task in self._creature_tasks:
            task.cancel()
        if self._creature_tasks:
            await asyncio.gather(*self._creature_tasks, return_exceptions=True)
        self._creature_tasks.clear()

        # Signal root agent to exit (its own run() handles cleanup)
        if self._root_agent is not None:
            self._root_agent._running = False

        # Stop each creature agent
        for handle in self._creatures.values():
            try:
                await handle.agent.stop()
                logger.info("Creature stopped", creature=handle.name)
            except Exception as exc:
                logger.error(
                    "Error stopping creature",
                    creature=handle.name,
                    error=str(exc),
                )

    async def run(self) -> None:
        """
        Run all creatures until interrupted or all stop.

        Each creature runs its own event loop as a concurrent task.
        The runtime waits for all tasks to finish (or cancellation).
        """
        await self.start()

        # Attach pending session store (set before run, applied after start)
        if self._pending_session_store:
            self.attach_session_store(self._pending_session_store)
            self._pending_session_store = None

        try:
            for handle in self._creatures.values():
                task = asyncio.create_task(
                    self._run_creature(handle),
                    name=f"creature_{handle.name}",
                )
                self._creature_tasks.append(task)

            # If root agent is present, configure TUI tabs and run
            if self._root_agent is not None:
                # Build tab list for TUI: root + creatures + channels
                tui_tabs = ["root"]
                tui_tabs.extend(h.name for h in self._creatures.values())
                for ch_info in self.list_channels():
                    tui_tabs.append(f"#{ch_info['name']}")
                # Store on the session for TUIInput to read
                if self._root_agent.session:
                    self._root_agent.session.extra["terrarium_tui_tabs"] = tui_tabs
                    self._root_agent.session.extra["terrarium_runtime"] = self

                root_task = asyncio.create_task(
                    self._root_agent.run(),
                    name="root_agent",
                )
                # Root agent is the primary: when user exits root, stop everything
                await root_task
            else:
                # No root: wait for all creature tasks
                await asyncio.gather(*self._creature_tasks, return_exceptions=True)
        except KeyboardInterrupt:
            logger.info("Terrarium interrupted")
        except asyncio.CancelledError:
            logger.info("Terrarium cancelled")
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Status / accessors
    # ------------------------------------------------------------------

    def attach_session_store(self, store: Any) -> None:
        """Attach a SessionStore to all creatures, root agent, and channels.

        Must be called AFTER start() (when creatures exist) but works
        at any time during the runtime lifecycle.
        """
        attach_session_store(self, store)

    @property
    def root_agent(self) -> Agent | None:
        """The root agent, if configured."""
        return self._root_agent

    @property
    def is_running(self) -> bool:
        """Whether the terrarium is currently running."""
        return self._running

    @property
    def creatures(self) -> dict[str, CreatureHandle]:
        """All creature handles, keyed by name."""
        return self._creatures

    @property
    def session_store(self) -> Any:
        """The attached SessionStore, or None."""
        return getattr(self, "_session_store", None)

    def get_creature_agent(self, name: str) -> Agent | None:
        """Get a creature's Agent instance by name."""
        handle = self._creatures.get(name)
        return handle.agent if handle else None

    def list_channels(self) -> list[dict[str, str]]:
        """List all shared channels as dicts."""
        return self.environment.shared_channels.get_channel_info()

    def get_status(self) -> dict[str, Any]:
        """Return a status dict for monitoring."""
        creature_states: dict[str, dict[str, Any]] = {}
        for name, handle in self._creatures.items():
            # Canonical ``provider/name[@variations]`` identifier so the
            # web ModelSwitcher pill survives a page refresh — the
            # per-request session_info event isn't replayed then.
            identifier = ""
            get_ident = getattr(handle.agent, "llm_identifier", None)
            if callable(get_ident):
                try:
                    identifier = get_ident() or ""
                except Exception:
                    identifier = ""
            creature_states[name] = {
                "running": handle.is_running,
                "model": getattr(handle.agent.llm, "model", "")
                or getattr(getattr(handle.agent.llm, "config", None), "model", ""),
                "llm_name": identifier,
                "listen_channels": handle.listen_channels,
                "send_channels": handle.send_channels,
            }

        channel_info: list[dict[str, str]] = []
        channel_info = self.environment.shared_channels.get_channel_info()

        # Working directory from root agent or first creature
        pwd = ""
        if self._root_agent and hasattr(self._root_agent, "executor"):
            pwd = str(self._root_agent.executor._working_dir)
        elif self._creatures:
            first = next(iter(self._creatures.values()))
            if hasattr(first.agent, "executor"):
                pwd = str(first.agent.executor._working_dir)

        status = {
            "name": self.config.name,
            "running": self._running,
            "has_root": self._root_agent is not None,
            "creatures": creature_states,
            "channels": channel_info,
            "pwd": pwd,
        }

        if self._root_agent:
            status["root_model"] = getattr(
                self._root_agent.llm, "model", ""
            ) or getattr(getattr(self._root_agent.llm, "config", None), "model", "")
            # Canonical ``provider/name[@variations]`` so the web pill
            # survives a refresh.
            root_identifier = ""
            get_ident = getattr(self._root_agent, "llm_identifier", None)
            if callable(get_ident):
                try:
                    root_identifier = get_ident() or ""
                except Exception:
                    root_identifier = ""
            status["root_llm_name"] = root_identifier
            status["root_session_id"] = ""
            if self._root_agent.session_store:
                try:
                    meta = self._root_agent.session_store.load_meta()
                    status["root_session_id"] = meta.get("session_id", "")
                except Exception as e:
                    logger.debug(
                        "Failed to load root session meta", error=str(e), exc_info=True
                    )
            compact = getattr(self._root_agent, "compact_manager", None)
            if compact:
                status["root_max_context"] = compact.config.max_tokens
                status["root_compact_threshold"] = int(
                    compact.config.max_tokens * compact.config.threshold
                )

        return status

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_creature(self, handle: CreatureHandle) -> None:
        """
        Run a single creature's event loop.

        Mirrors ``Agent.run()`` but without calling ``start()`` / ``stop()``
        (those are managed by the runtime). Also handles the two resume
        steps that ``Agent.run()`` does — output-event replay and
        trigger restore — since creatures skip ``Agent.run()`` entirely.
        Without this, resumed terrariums had broken creature output
        replay and hot-plugged triggers were lost.
        """
        agent = handle.agent

        try:
            # Replay recorded output events (tool calls, messages, etc.)
            # to this creature's output_router — the web/TUI rebuilds
            # per-creature scrollback from these.
            if getattr(agent, "_pending_resume_events", None):
                try:
                    await agent.output_router.emit(
                        OutputEvent(
                            type="resume_batch",
                            payload={"events": agent._pending_resume_events},
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Creature output replay failed",
                        creature=handle.name,
                        error=str(exc),
                    )
                agent._pending_resume_events = None

            # Re-create resumable triggers (hot-plugged channel
            # listeners, timers, etc.) from saved state.
            pending_triggers = getattr(agent, "_pending_resume_triggers", None)
            if pending_triggers:
                try:
                    await agent._restore_triggers(pending_triggers)
                except Exception as exc:
                    logger.warning(
                        "Creature trigger restore failed",
                        creature=handle.name,
                        error=str(exc),
                    )
                agent._pending_resume_triggers = None

            # Fire startup trigger if configured
            await agent._fire_startup_trigger()

            idle_logged = False
            while agent._running:
                if not idle_logged:
                    logger.debug(
                        "Creature idle, waiting for input",
                        creature=handle.name,
                    )
                    idle_logged = True

                event = await agent.input.get_input()

                if event is None:
                    if (
                        hasattr(agent.input, "exit_requested")
                        and agent.input.exit_requested
                    ):
                        logger.info("Creature exit requested", creature=handle.name)
                        break
                    continue

                idle_logged = False
                logger.info(
                    "Creature received input",
                    creature=handle.name,
                    event_type=event.type,
                )
                await agent._process_event(event)

        except asyncio.CancelledError:
            logger.info("Creature task cancelled", creature=handle.name)
        except Exception as exc:
            logger.error(
                "Creature error",
                creature=handle.name,
                error=str(exc),
            )
            raise
