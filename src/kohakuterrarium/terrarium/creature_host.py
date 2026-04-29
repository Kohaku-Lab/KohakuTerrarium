"""Unified creature wrapper used by the new Terrarium engine.

Combines today's split between ``serving.AgentSession`` (standalone
agent wrapper used by ``KohakuManager``) and
``terrarium.creature.CreatureHandle`` (channel-aware wrapper used by
``TerrariumRuntime``).  In the new model **every** running agent is a
:class:`Creature`; standalone agents are creatures in 1-creature
graphs.

Includes the wrapper plus a ``build_creature`` factory that handles
both ``AgentConfig`` (file path or object) and ``CreatureConfig``
(in-recipe shape) inputs.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import AgentConfig, build_agent_config
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.llm.profiles import _login_provider_for
from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Creature — the engine's per-running-agent wrapper
# ---------------------------------------------------------------------------


@dataclass
class Creature:
    """A running agent inside the Terrarium engine.

    A solo `kt run` creates one of these in a 1-creature graph; a
    terrarium recipe creates several in one graph wired by channels.
    The class combines today's ``AgentSession`` + ``CreatureHandle``
    surfaces so routes / CLI / programmatic users only need one type.

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
    listen_channels: list[str] = field(default_factory=list)
    send_channels: list[str] = field(default_factory=list)
    output_log: OutputLogCapture | None = None
    # Set by ``Terrarium.assign_root``.  Read by higher-level code that
    # mounts user IO on the root creature or force-registers terrarium
    # tools on it.
    is_root: bool = False

    # Internal queue for chat() output streaming.  Created lazily so
    # the dataclass stays trivially constructible.
    _output_queue: "asyncio.Queue[str | None] | None" = None
    _running: bool = False
    _chat_handler_installed: bool = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying agent.  Idempotent."""
        if self._running:
            return
        self._ensure_chat_pipe()
        await self.agent.start()
        self._running = True
        logger.info(
            "Creature started", creature_id=self.creature_id, creature_name=self.name
        )

    async def stop(self) -> None:
        """Stop the underlying agent and close the chat pipe."""
        if not self._running:
            return
        self._running = False
        if self._output_queue is not None:
            self._output_queue.put_nowait(None)
        await self.agent.stop()
        logger.info(
            "Creature stopped", creature_id=self.creature_id, creature_name=self.name
        )

    @property
    def is_running(self) -> bool:
        return self._running and self.agent.is_running

    # ------------------------------------------------------------------
    # chat — streaming inject_input + output drain
    # ------------------------------------------------------------------

    async def inject_input(
        self,
        message: str | list[dict],
        *,
        source: str = "chat",
    ) -> None:
        """Push input into the agent without consuming output."""
        await self.agent.inject_input(message, source=source)

    async def chat(self, message: str | list[dict]) -> AsyncIterator[str]:
        """Inject ``message`` and stream the agent's text response.

        Yields chunks until the agent finishes processing the input.
        Mirrors today's ``AgentSession.chat`` semantics so HTTP / WS
        callers don't have to change.
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
        """Return a status dict matching ``AgentSession.get_status()``.

        Every field today's frontend reads is included — model,
        max_context, compact_threshold, provider, session_id, tools,
        subagents, pwd.
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
                logger.debug("llm_identifier resolve failed", error=str(e))
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
                logger.debug(
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
            "model": model,
            "llm_name": llm_identifier,
            "provider": provider,
            "session_id": session_id,
            "max_context": max_context,
            "compact_threshold": compact_threshold,
            "running": self.is_running,
            "is_processing": bool(getattr(agent, "_processing_task", None)),
            "tools": agent.tools,
            "subagents": agent.subagents,
            "pwd": pwd,
            "listen_channels": list(self.listen_channels),
            "send_channels": list(self.send_channels),
        }

    # ------------------------------------------------------------------
    # output log helpers — mirror CreatureHandle's surface
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


def build_creature(
    config: CreatureBuildInput,
    *,
    creature_id: str | None = None,
    graph_id: str = "",
    pwd: str | None = None,
    llm_override: str | None = None,
    environment: Environment | None = None,
) -> Creature:
    """Build a :class:`Creature` from any of the supported config shapes.

    Covers the no-channel path (solo creature, terrarium environment
    defaulted).  Channel injection happens later when the creature is
    connected via the engine's ``connect`` API.

    Accepted ``config`` types:

    - ``str`` / ``Path`` — path to a creature config file.  Loaded via
      ``Agent.from_path``.
    - ``AgentConfig`` — already-loaded standalone config.  Wrapped via
      ``Agent(config, ...)``.
    - ``CreatureConfig`` — in-recipe creature dict.  Loaded via
      ``build_agent_config(config_data, base_dir)`` then ``Agent(...)``.
    """
    if isinstance(config, (str, Path)):
        agent = Agent.from_path(
            str(config),
            session=(
                environment.get_session(creature_id or Path(config).stem)
                if environment is not None
                else None
            ),
            environment=environment,
            llm_override=llm_override,
            pwd=pwd,
        )
        cid = creature_id or _safe_creature_id(agent.config.name)
        return Creature(
            creature_id=cid,
            name=agent.config.name,
            agent=agent,
            graph_id=graph_id,
            config=agent.config,
        )

    if isinstance(config, AgentConfig):
        session = (
            environment.get_session(creature_id or config.name) if environment else None
        )
        agent = Agent(
            config,
            session=session,
            environment=environment,
            llm_override=llm_override,
            pwd=pwd,
        )
        cid = creature_id or _safe_creature_id(config.name)
        return Creature(
            creature_id=cid,
            name=config.name,
            agent=agent,
            graph_id=graph_id,
            config=config,
        )

    if isinstance(config, CreatureConfig):
        agent_config = build_agent_config(config.config_data, config.base_dir)
        agent = Agent(
            agent_config,
            input_module=NoneInput(),
            session=environment.get_session(config.name) if environment else None,
            environment=environment,
            llm_override=llm_override,
            pwd=pwd,
        )
        cid = creature_id or _safe_creature_id(config.name)
        return Creature(
            creature_id=cid,
            name=config.name,
            agent=agent,
            graph_id=graph_id,
            config=config,
            listen_channels=list(config.listen_channels),
            send_channels=list(config.send_channels),
        )

    raise TypeError(
        f"build_creature: unsupported config type {type(config).__name__!r}"
    )


def _safe_creature_id(name: str) -> str:
    """Mint a unique creature id from a config name.

    Names from a recipe are usually meaningful and unique within the
    recipe, but the engine namespace is process-wide — append a short
    random suffix so two recipes with the same creature name don't
    collide.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return f"{cleaned or 'creature'}_{uuid4().hex[:8]}"
