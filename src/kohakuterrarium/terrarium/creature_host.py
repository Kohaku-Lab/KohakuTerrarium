"""Creature wrapper for the Terrarium engine.

Every running agent is a :class:`Creature`; a standalone ``kt run`` is
a creature in a 1-creature graph, a recipe is several creatures in
one graph wired by channels.

Includes the wrapper plus a ``build_creature`` factory that handles
both ``AgentConfig`` (file path or object) and ``CreatureConfig``
(in-recipe shape) inputs.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import (
    AgentConfig,
    build_agent_config,
    load_agent_config,
)
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.turn import AgentEventStream, TurnResult
from kohakuterrarium.llm.profiles import _login_provider_for
from kohakuterrarium.packages.walk import package_snapshot
from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.creature_ids import _safe_creature_id
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Engine bookkeeping that prevents Drive delivery until startup has settled;
# this state is not part of the agent's cognitive state.
RESTORATION_ADDED = "added"
RESTORATION_RESTORING = "restoring"
RESTORATION_STARTED = "started"
RESTORATION_STARTUP_SETTLED = "startup_settled"
RESTORATION_READY = "restoration_ready"


# ---------------------------------------------------------------------------
# Creature — the engine's per-running-agent wrapper
# ---------------------------------------------------------------------------


@dataclass
class Creature:
    """A running agent inside the Terrarium engine.

    A solo `kt run` creates one of these in a 1-creature graph; a
    terrarium recipe creates several in one graph wired by channels.
    Every per-creature endpoint (HTTP, WS, CLI, tools) reads from this
    one type.

    Programmatic usage::

        async with Terrarium() as t:
            alice = await t.add_creature("creatures/alice.yaml")
            async for chunk in alice.chat("hello"):
                print(chunk, end="", flush=True)
    """

    creature_id: str
    name: str
    agent: Agent
    graph_id: str = ""
    config: Any = None
    config_snapshot: dict[str, Any] | None = None
    source_ref: str | None = None
    config_name: str = ""
    config_ref: str | None = None
    build_pwd: str = ""
    injected_runtime: tuple[str, ...] = ()
    listen_channels: list[str] = field(default_factory=list)
    send_channels: list[str] = field(default_factory=list)
    output_log: OutputLogCapture | None = None
    # Privilege is elevate-only: set once (at creation or via
    # ``Terrarium.assign_root``) and never demoted thereafter. True
    # for creatures created by direct user action (solo ``kt run``,
    # Studio "new creature") and recipe roots elevated by
    # ``assign_root``. False for recipe non-root creatures and for
    # workers spawned via ``group_add_node``.
    is_privileged: bool = False
    # Creature_id of the privileged creature that spawned this one via
    # ``group_add_node``. None for user-created or recipe-created
    # creatures. Used by ``group_status`` to surface workers that the
    # caller spawned but hasn't wired into a graph yet.
    parent_creature_id: str | None = None

    # Internal queue for chat() output streaming.  Created lazily so
    # the dataclass stays trivially constructible.
    _output_queue: "asyncio.Queue[str | None] | None" = None
    _running: bool = False
    _stop_requested: bool = True
    _lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _chat_handler_installed: bool = False
    # Background task driving the agent's configured input module.
    # Spawned in ``start`` and reaped in ``stop`` — see ``start`` for
    # why this lives at the creature layer rather than inside Agent.
    _input_task: "asyncio.Task[None] | None" = None
    # Has ``start()`` ever been called? Used to distinguish "not yet
    # started" from "stopped" in :attr:`status`. ``_running`` alone
    # cannot tell them apart because both states leave it ``False``.
    _ever_started: bool = False
    # If the input-driver task exits with an exception, the exception
    # is captured here so :attr:`status` can report ``"error"`` until
    # the next ``start()`` clears it.
    _input_loop_error: BaseException | None = None
    # Force-stop marker distinguishes a creature that was killed from one
    # plainly stopped. Cleared on the next start().
    _killed: bool = False
    # The restoration barrier is runtime-only readiness state that the engine's
    # Drive runtime gates reconciliation on. Transitions:
    # added -> restoring -> started -> startup_settled -> restoration_ready.
    _restoration_state: str = RESTORATION_ADDED
    _restoration_ready_event: "asyncio.Event | None" = None
    _restoration_task: "asyncio.Task[None] | None" = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, requested: bool = True) -> None:
        """Start the underlying agent.  Idempotent.

        Also spawns ``Agent._drive_input`` as a background task so the
        configured input module's polling loop is driven by the engine
        path (the standalone ``Agent.run`` path drives it directly).
        Without this, headless agents (Discord bot, webhook listener,
        custom polling input) never consume their queues — their
        ``get_input`` is the only place ``_process_event`` is reached
        for non-trigger, non-channel input. Cheap for ``NoneInput``-
        backed creatures: ``get_input`` blocks on the stop event.

        The ``_drive_input`` lookup is tolerant — agent-likes used in
        tests or specialized hosts that don't expose this hook simply
        skip the spawn (they're presumed to drive their own loop).
        """
        async with self._lifecycle_lock:
            if self._running:
                return
            if requested:
                self._stop_requested = False
            elif self._stop_requested:
                return
            self._ensure_chat_pipe()
            self._restoration_state = RESTORATION_RESTORING
            try:
                await self.agent.start()
            except BaseException:
                self._restoration_state = RESTORATION_ADDED
                try:
                    await self.agent.stop()
                except Exception as cleanup_error:
                    logger.error(
                        "Creature startup rollback failed",
                        creature_id=self.creature_id,
                        creature_name=self.name,
                        error=str(cleanup_error),
                        exc_info=True,
                    )
                raise
            self._running = True
            self._ever_started = True
            self._input_loop_error = None
            self._killed = False
            self._restoration_state = RESTORATION_STARTED
            drive_input = getattr(self.agent, "_drive_input", None)
            if callable(drive_input):
                self._input_task = asyncio.create_task(
                    drive_input(),
                    name=f"creature-input-{self.creature_id}",
                )
                self._input_task.add_done_callback(self._on_input_task_done)
            self._arm_restoration_barrier()
            logger.info(
                "Creature started",
                creature_id=self.creature_id,
                creature_name=self.name,
            )

    def _ensure_restoration_event(self) -> "asyncio.Event":
        if self._restoration_ready_event is None:
            self._restoration_ready_event = asyncio.Event()
        return self._restoration_ready_event

    def _arm_restoration_barrier(self) -> None:
        """Spawn the task that waits for the agent's startup trigger to
        settle, then flips the creature to ``restoration_ready``."""
        self._ensure_restoration_event().clear()
        self._restoration_task = asyncio.create_task(
            self._run_restoration_barrier(),
            name=f"creature-barrier-{self.creature_id}",
        )

    async def _run_restoration_barrier(self) -> None:
        """Await startup-trigger settlement, then mark restoration-ready.

        An agent-like without the ``_startup_settled`` observable (test
        fakes) is treated as settled immediately — there is no startup
        turn to wait for."""
        settled = getattr(self.agent, "_startup_settled", None)
        if settled is not None and hasattr(settled, "wait"):
            try:
                await settled.wait()
            except asyncio.CancelledError:
                return
        self._restoration_state = RESTORATION_STARTUP_SETTLED
        self._restoration_state = RESTORATION_READY
        self._ensure_restoration_event().set()

    @property
    def restoration_state(self) -> str:
        """Current restoration-barrier state."""
        return self._restoration_state

    @property
    def restoration_ready(self) -> bool:
        """Whether the restoration barrier has been crossed — Drive
        reconciliation for this creature is gated on it."""
        return self._restoration_state == RESTORATION_READY

    async def wait_restoration_ready(self) -> None:
        """Await restoration unless lifecycle stop intent supersedes it."""
        if self.restoration_ready or self._stop_requested:
            return
        await self._ensure_restoration_event().wait()

    def _on_input_task_done(self, task: "asyncio.Task[None]") -> None:
        """Mark the creature host stopped once its input loop exits.

        The loop ends naturally when the input module signals
        ``exit_requested``, when ``Agent.stop`` flips ``_running``, or
        when an unexpected exception escapes. Flipping ``_running``
        here lets external lifecycle drivers (``kt run`` sleep loop,
        engine ``__aexit__``) notice and proceed to ``stop``.
        """
        if task.cancelled():
            self._running = False
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            logger.error(
                "Creature input loop exited with error",
                creature_id=self.creature_id,
                creature_name=self.name,
                error=str(exc),
            )
            self._input_loop_error = exc
        self._running = False

    async def stop(self, *, requested: bool = True) -> None:
        """Stop the underlying agent and close the chat pipe."""
        if requested:
            self._stop_requested = True
        async with self._lifecycle_lock:
            if not self._running and self._input_task is None:
                return
            self._running = False
            self._restoration_state = RESTORATION_ADDED
            self._teardown_restoration_barrier()
            if self._output_queue is not None:
                self._output_queue.put_nowait(None)
            # Stopping the agent flips ``Agent._running`` and stops the
            # input module, which unblocks ``get_input`` and lets the
            # background loop exit on its own.
            await self.agent.stop()
            await self._reap_input_task()
            logger.info(
                "Creature stopped",
                creature_id=self.creature_id,
                creature_name=self.name,
            )

    def _teardown_restoration_barrier(self) -> None:
        """Cancel restoration and release waiters superseded by stop."""
        task = self._restoration_task
        self._restoration_task = None
        if task is not None and not task.done():
            task.cancel()
        if self._restoration_ready_event is not None:
            self._restoration_ready_event.set()

    async def _reap_input_task(self) -> None:
        """Wait for the input-driver task to exit, cancelling on timeout."""
        task = self._input_task
        if task is None:
            return
        self._input_task = None
        if task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(
                    "input task cancel ended with exception",
                    creature_id=self.creature_id,
                    error=str(e),
                    exc_info=True,
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "input task raised on stop",
                creature_id=self.creature_id,
                error=str(e),
                exc_info=True,
            )

    @property
    def is_running(self) -> bool:
        """Convenience shortcut over the ground-truth :attr:`status`: the
        creature is alive and message-eligible (``status`` is ``"idle"`` or
        ``"busy"``).  ``status`` is authoritative; this is derived from it."""
        return self.status in ("idle", "busy")

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def is_naturally_idle(self) -> bool:
        if self._stop_requested or self.agent.is_running:
            return False
        if self._turn_work_pending():
            return False
        return not self._background_work_pending()

    def _turn_work_pending(self) -> bool:
        turn_lock = getattr(self.agent, "_turn_lock", None)
        if turn_lock is not None and turn_lock.locked():
            return True
        processing = getattr(self.agent, "_processing_task", None)
        if processing is not None and not processing.done():
            return True
        active_turn = getattr(self.agent, "_active_turn_task", None)
        if active_turn is not None and not active_turn.done():
            return True
        inbox = getattr(self.agent, "_event_inbox", None)
        return inbox is not None and not inbox.empty()

    def _background_work_pending(self) -> bool:
        if getattr(self.agent, "_active_handles", None):
            return True
        executor = getattr(self.agent, "executor", None)
        if executor is not None and executor.get_running_jobs():
            return True
        manager = getattr(self.agent, "subagent_manager", None)
        return manager is not None and bool(manager.get_running_jobs())

    @property
    def status(self) -> str:
        """Lifecycle status — what the creature is doing right now.

        Replacement for the legacy ``running: bool`` view used by
        ``group_status``. ``running`` collapsed several distinct states
        (never-started, mid-turn, idle-waiting, stopped, crashed) into a
        single boolean that flipped to ``False`` whenever the input
        loop exited, even on clean stop — so a privileged caller could
        not tell whether a worker was idle, busy, or dead.

        Values:

        - ``"not_started"``: ``start()`` has never completed.
        - ``"error"``: the input loop exited with an exception that
          was not a normal cancel. Cleared on the next ``start()``.
        - ``"busy"``: a controller turn is in flight (the agent has a
          live ``_processing_task``).
        - ``"idle"``: the agent is alive and waiting for input / a
          trigger / a channel message.
        - ``"stopped"``: the agent is no longer running (``stop()`` was
          called, or a standalone ``run_forever`` loop finished); it is no
          longer servicing events. NOTE: an engine worker whose ``_drive_input``
          task idles out keeps a live agent (``agent.is_running`` stays True),
          so it stays ``"idle"`` — still able to receive channel / group_send
          messages — not ``"stopped"``.
        """
        if not self._ever_started:
            return "not_started"
        if self._input_loop_error is not None:
            return "error"
        if not self.agent.is_running:
            return "stopped"
        proc = getattr(self.agent, "_processing_task", None)
        if proc is not None and not proc.done():
            return "busy"
        return "idle"

    @property
    def paused(self) -> bool:
        """Whether the creature is warm-paused. Orthogonal to
        :attr:`status`/:attr:`is_running` — a paused creature stays alive
        and warm, it just admits no new turns until resumed."""
        return bool(getattr(self.agent, "_paused", False))

    @property
    def killed(self) -> bool:
        """Whether this creature was force-stopped via a kill operation.
        Cleared on the next :meth:`start`."""
        return self._killed

    def pause(self) -> None:
        """Warm-pause the underlying agent (thin delegate — see
        :meth:`Agent.pause`)."""
        self.agent.pause()

    def resume(self) -> None:
        """Resume the underlying agent from a warm pause (thin delegate —
        see :meth:`Agent.resume`)."""
        self.agent.resume()

    # ------------------------------------------------------------------
    # typed turn drivers — the programmatic chat surface
    # ------------------------------------------------------------------

    async def run(self, content, **kwargs):
        """Drive one full turn; return a
        :class:`~kohakuterrarium.core.turn.TurnResult`.

        Delegates to :meth:`Agent.run` — see it for ``timeout=`` /
        ``raise_on_error=`` semantics (a failed turn RAISES by default,
        a timed-out one is actually interrupted).
        """
        return await self.agent.run(content, **kwargs)

    def run_stream(self, content, **kwargs):
        """Drive one turn, yielding typed
        :class:`~kohakuterrarium.core.turn.TurnEvent`\\ s live
        (``TextChunk`` / ``Activity`` / final ``TurnEnded``).
        """
        return self.agent.run_stream(content, **kwargs)

    def attach(self) -> AgentEventStream:
        """Observe this creature's output as typed events.

        Non-destructive and multi-consumer: every ``attach()`` gets its
        own stream; the default output, session store, and concurrent
        turns are unaffected.  Captures out-of-band turns (triggers,
        channel messages) too — everything the output router fans out.

        Usage::

            async with creature.attach() as stream:
                async for ev in stream:
                    ...
        """
        return AgentEventStream(self.agent)

    # ------------------------------------------------------------------
    # chat — streaming inject_input + output drain (text-only sugar)
    # ------------------------------------------------------------------

    async def inject_input(
        self,
        message: str | list[dict],
        *,
        source: str = "chat",
    ) -> None:
        """Push input into the agent without consuming output."""
        await self.agent.inject_input(message, source=source)

    async def inject_event(
        self,
        event: TriggerEvent,
        *,
        correlation_id: str | None = None,
        timeout: float | None = None,
        raise_on_error: bool = False,
    ) -> TurnResult:
        """Drive a pre-built :class:`TriggerEvent` and return its correlated
        :class:`TurnResult` through the public creature ingress.

        This is the seam the Terrarium Drive dispatcher delivers over. It
        distinguishes *rejected-because-stopped* (``status="rejected"``,
        never silent) from an admitted turn that settled
        ``ok``/``error``/``timeout``/``interrupted``; ``correlation_id``
        (the delivery id) rides ``event.context`` to turn finalization and
        back onto the result.  No private ``Agent._process_event`` call.
        """
        if correlation_id is not None:
            if event.context is None:
                event.context = {}
            event.context.setdefault("correlation_id", correlation_id)
        return await self.agent.run_event(
            event, timeout=timeout, raise_on_error=raise_on_error
        )

    async def chat(self, message: str | list[dict]) -> AsyncIterator[str]:
        """Inject ``message`` and stream the agent's text response.

        Yields chunks until the agent finishes processing the input.
        Used by HTTP / WS chat endpoints and by tests.
        """
        self._ensure_chat_pipe()
        q = self._output_queue
        assert q is not None
        # Drop any stale chunks from before this turn.
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break
        inject_task = asyncio.create_task(
            self.agent.inject_input(message, source="chat")
        )
        while not inject_task.done():
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if chunk is None:
                break
            yield chunk
        # Drain anything that landed after the inject completed.
        while not q.empty():
            try:
                chunk = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if chunk is None:
                break
            yield chunk
        await inject_task

    def _ensure_chat_pipe(self) -> None:
        """Lazily wire the agent's output handler to our queue."""
        if self._output_queue is None:
            self._output_queue = asyncio.Queue()
        if not self._chat_handler_installed:
            self.agent.set_output_handler(self._on_output_chunk)
            self._chat_handler_installed = True

    def _on_output_chunk(self, text: str) -> None:
        if self._output_queue is None:
            return
        self._output_queue.put_nowait(text)

    # ------------------------------------------------------------------
    # status — preserves the dict shape today's frontend reads
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return a status dict for HTTP / WS / TUI consumers.

        Every field the frontend reads is included — model,
        max_context, compact_threshold, provider, session_id, tools,
        subagents, pwd, plus ``is_privileged`` /
        ``parent_creature_id`` for the group-tools view.
        """
        agent = self.agent
        model = (
            getattr(agent.llm, "model", "")
            or getattr(getattr(agent.llm, "config", None), "model", "")
            or agent.config.model
        )
        llm_identifier = ""
        get_ident = getattr(agent, "llm_identifier", None)
        if callable(get_ident):
            try:
                llm_identifier = get_ident() or ""
            except Exception as e:
                logger.warning(
                    "llm_identifier resolve failed", error=str(e), exc_info=True
                )
        max_context = getattr(agent.llm, "_profile_max_context", 0)
        compact_threshold = 0
        if agent.compact_manager and max_context:
            compact_threshold = int(
                max_context * agent.compact_manager.config.threshold
            )

        profile_data: dict[str, str] = {"provider": getattr(agent.llm, "provider", "")}
        api_key_env = getattr(agent.llm, "api_key_env", "")
        if api_key_env:
            profile_data["api_key_env"] = api_key_env
        base_url = getattr(agent.llm, "base_url", "")
        if base_url:
            profile_data["base_url"] = base_url
        provider = _login_provider_for(profile_data)

        session_id = ""
        if agent.session_store:
            try:
                meta = agent.session_store.load_meta()
                session_id = meta.get("session_id", "")
            except Exception as e:
                logger.warning(
                    "Failed to load session meta",
                    error=str(e),
                    exc_info=True,
                )

        pwd = ""
        if hasattr(agent, "executor") and agent.executor:
            pwd = str(agent.executor._working_dir)

        return {
            "agent_id": self.creature_id,
            "creature_id": self.creature_id,
            "graph_id": self.graph_id,
            "name": self.name,
            "config_name": self.config_name,
            "config_ref": self.config_ref,
            "model": model,
            "llm_name": llm_identifier,
            "provider": provider,
            "session_id": session_id,
            "max_context": max_context,
            "compact_threshold": compact_threshold,
            "running": self.is_running,
            "paused": self.paused,
            "killed": self._killed,
            "is_processing": bool(getattr(agent, "_processing_task", None)),
            "tools": agent.tools,
            "subagents": agent.subagents,
            "pwd": pwd,
            "listen_channels": list(self.listen_channels),
            "send_channels": list(self.send_channels),
            "is_privileged": self.is_privileged,
            "parent_creature_id": self.parent_creature_id,
        }

    # ------------------------------------------------------------------
    # output log helpers
    # ------------------------------------------------------------------

    def get_log_entries(self, last_n: int = 20) -> list[LogEntry]:
        if self.output_log:
            return self.output_log.get_entries(last_n=last_n)
        return []

    def get_log_text(self, last_n: int = 10) -> str:
        if self.output_log:
            return self.output_log.get_text(last_n=last_n)
        return ""


# ---------------------------------------------------------------------------
# build_creature — accepts the three input shapes the engine sees
# ---------------------------------------------------------------------------


CreatureBuildInput = AgentConfig | CreatureConfig | str | Path


def _with_terrarium_plugin_defaults(config: AgentConfig) -> AgentConfig:
    """Enable Terrarium-wide plugin defaults without importing implementations."""
    defaults = list(config.default_plugins)
    if "goal" not in defaults:
        defaults.append("goal")
    config.default_plugins = defaults
    return config


def _runtime_injection_labels(llm, tools, plugins) -> tuple[str, ...]:
    return tuple(
        label
        for label, value in (
            ("llm_provider", llm if not isinstance(llm, (str, type(None))) else None),
            ("tools", tools),
            ("plugins", plugins),
        )
        if value
    )


def _build_provenance(
    agent: Agent,
    *,
    source_ref: str | None,
    injected_runtime: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Capture the resolved declarative inputs needed to rebuild a creature."""
    try:
        config_snapshot = pack_agent_config(agent.config)
    except TypeError:
        config_snapshot = None
    return {
        "config_snapshot": config_snapshot,
        "source_ref": source_ref,
        "config_name": str(getattr(agent.config, "name", "") or ""),
        "config_ref": source_ref,
        "build_pwd": str(
            getattr(getattr(agent, "executor", None), "_working_dir", None)
            or os.getcwd()
        ),
        "injected_runtime": injected_runtime,
    }


def apply_creature_name(creature: "Creature", name: str) -> None:
    """Push a display-name change onto every nested object that caches it.

    Setting ``creature.name`` alone is not enough: the executor (and its
    ToolContexts) keep emitting channel messages under the original
    config name, the trigger manager logs with the old name, the compact
    manager too. ``engine.add_creature`` uses this to apply a spawn-time
    ``name`` override consistently; ``studio.sessions.lifecycle`` calls it
    for post-spawn renames.
    """
    creature.name = name
    agent = getattr(creature, "agent", None)
    if agent is None:
        if getattr(creature, "config", None) is not None:
            creature.config.name = name
        return
    if getattr(agent, "config", None) is not None:
        agent.config.name = name
    if getattr(creature, "config", None) is not None:
        creature.config.name = name
    executor = getattr(agent, "executor", None)
    if executor is not None and hasattr(executor, "_agent_name"):
        executor._agent_name = name
    trigger_manager = getattr(agent, "trigger_manager", None)
    if trigger_manager is not None and hasattr(trigger_manager, "_agent_name"):
        trigger_manager._agent_name = name
    compact_manager = getattr(agent, "compact_manager", None)
    if compact_manager is not None and hasattr(compact_manager, "_agent_name"):
        compact_manager._agent_name = name
    subagent_manager = getattr(agent, "subagent_manager", None)
    if subagent_manager is not None and hasattr(subagent_manager, "_parent_name"):
        subagent_manager._parent_name = name
    # A SessionOutput attached BEFORE the rename keeps recording events
    # under the old name — but the history/read side resolves events by
    # the creature's display name, so the rename must follow it onto the
    # recorder. Only retarget the prefix when it still equals the old
    # name: attached-agent recorders use custom ``<host>:attached:...``
    # prefixes that a rename must not clobber.
    session_output = getattr(agent, "_session_output", None)
    if session_output is not None:
        old = getattr(session_output, "_agent_name", None)
        session_output._agent_name = name
        if getattr(session_output, "_event_key_prefix", None) == old:
            session_output._event_key_prefix = name


def build_creature(
    config: CreatureBuildInput,
    *,
    creature_id: str | None = None,
    graph_id: str = "",
    pwd: str | None = None,
    llm: Any = None,
    environment: Environment | None = None,
    io: str = "config",
    strict: bool = True,
    tools: list[Any] | None = None,
    plugins: list[Any] | None = None,
) -> Creature:
    """Build a :class:`Creature` from any of the supported config shapes.

    Covers the no-channel path (solo creature, terrarium environment
    defaulted).  Channel injection happens later when the creature is
    connected via the engine's ``connect`` API.

    Accepted ``config`` types:

    - ``str`` / ``Path`` — path to a creature config file (or a
      ``@pkg/...`` reference).  Loaded via ``Agent.from_path``.
    - ``AgentConfig`` — already-loaded standalone config.  Wrapped via
      ``Agent(config, ...)``.
    - ``CreatureConfig`` — in-recipe creature dict.  Loaded via
      ``build_agent_config(config_data, base_dir)`` then ``Agent(...)``.

    ``io`` selects how much of the config's I/O boots:

    - ``"config"`` — the config's input + output run as declared (the
      standalone ``kt run`` path).
    - ``"none"`` — input forced to :class:`NoneInput`; config output
      still boots.  A creature managed by the Studio / Lab layer is
      driven entirely through the attach WebSocket — it must NEVER
      boot its config's own ``input: cli`` loop, which on a worker
      process would hijack the terminal's stdin.
    - ``"headless"`` — input forced to :class:`NoneInput` AND the
      default output forced to :class:`NoneOutput`.  For batch /
      programmatic runs: agent text is consumed via ``run_stream`` /
      the session store, and N concurrent agents must not interleave
      writes on the console.  Named outputs from the config are
      preserved.
    """
    if io not in ("config", "none", "headless"):
        raise ValueError(f"io= must be 'config', 'none', or 'headless' — got {io!r}")
    with package_snapshot():
        return _build_creature_from_config(
            config,
            creature_id=creature_id,
            graph_id=graph_id,
            pwd=pwd,
            llm=llm,
            environment=environment,
            io=io,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )


def _build_creature_from_config(
    config: CreatureBuildInput,
    *,
    creature_id: str | None,
    graph_id: str,
    pwd: str | None,
    llm: Any,
    environment: Environment | None,
    io: str,
    strict: bool,
    tools: list[Any] | None,
    plugins: list[Any] | None,
) -> Creature:
    _io_override = NoneInput() if io in ("none", "headless") else None
    _out_override = NoneOutput() if io == "headless" else None
    if isinstance(config, (str, Path)):
        agent_config = _with_terrarium_plugin_defaults(load_agent_config(config))
        agent = Agent(
            agent_config,
            input_module=_io_override,
            output_module=_out_override,
            session=(
                environment.get_session(creature_id or Path(config).stem)
                if environment is not None
                else None
            ),
            environment=environment,
            llm=llm,
            pwd=pwd,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )
        cid = creature_id or _safe_creature_id(agent.config.name)
        return Creature(
            creature_id=cid,
            name=agent.config.name,
            agent=agent,
            graph_id=graph_id,
            config=agent.config,
            **_build_provenance(
                agent,
                source_ref=str(config),
                injected_runtime=_runtime_injection_labels(llm, tools, plugins),
            ),
        )

    if isinstance(config, AgentConfig):
        config = _with_terrarium_plugin_defaults(config)
        session = (
            environment.get_session(creature_id or config.name) if environment else None
        )
        agent = Agent(
            config,
            input_module=_io_override,
            output_module=_out_override,
            session=session,
            environment=environment,
            llm=llm,
            pwd=pwd,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )
        cid = creature_id or _safe_creature_id(config.name)
        return Creature(
            creature_id=cid,
            name=config.name,
            agent=agent,
            graph_id=graph_id,
            config=config,
            **_build_provenance(
                agent,
                source_ref=None,
                injected_runtime=_runtime_injection_labels(llm, tools, plugins),
            ),
        )

    if isinstance(config, CreatureConfig):
        agent_config = _with_terrarium_plugin_defaults(
            build_agent_config(config.config_data, config.base_dir)
        )
        # CreatureConfig (in-recipe / hot-plug) is always engine-managed
        # and channel-driven — its input is suppressed unconditionally;
        # ``io="headless"`` additionally silences the default output.
        agent = Agent(
            agent_config,
            input_module=NoneInput(),
            output_module=_out_override,
            session=environment.get_session(config.name) if environment else None,
            environment=environment,
            llm=llm,
            pwd=pwd,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )
        cid = creature_id or _safe_creature_id(config.name)
        return Creature(
            creature_id=cid,
            name=config.name,
            agent=agent,
            graph_id=graph_id,
            config=config,
            **_build_provenance(
                agent,
                source_ref=(
                    str(config.config_data.get("base_config"))
                    if config.config_data.get("base_config")
                    else None
                ),
                injected_runtime=_runtime_injection_labels(llm, tools, plugins),
            ),
            listen_channels=list(config.listen_channels),
            send_channels=list(config.send_channels),
        )

    raise TypeError(
        f"build_creature: unsupported config type {type(config).__name__!r}"
    )


# ``_safe_creature_id`` / ``_clean_creature_name`` / ``_decode_creature_name``
# live in the leaf :mod:`terrarium.creature_ids` (shared with the Drive resume
# remap without cross-importing the heavy Agent graph); re-exported here for
# existing callers (``resume.py`` imports ``_safe_creature_id`` from here).
