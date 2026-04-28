"""Agent wrappers — bridge between compose algebra and live agents.

Two modes:
- ``AgentRunnable``: persistent session, reused across calls
- ``AgentFactory``: ephemeral, creates a fresh agent per call

Convenience constructors:
- ``await agent(config_or_path)`` → AgentRunnable (starts immediately)
- ``factory(config_or_path)`` → AgentFactory (lazy, no startup cost)

Each runnable accepts any object with the chat-session protocol:
``.chat(message) -> AsyncIterator[str]`` and ``async .stop()``.
The convenience helpers below adapt a :class:`Terrarium` creature into
that shape so user code that previously relied on ``AgentSession``
keeps working unchanged.
"""

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from kohakuterrarium.compose.core import BaseRunnable
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class _ChatSession(Protocol):
    """Minimal chat-session protocol the compose runnables consume."""

    agent_id: str

    def chat(self, message: str) -> AsyncIterator[str]: ...

    async def stop(self) -> None: ...


class AgentRunnable(BaseRunnable):
    """Persistent agent — starts once, reused across calls.

    Conversation history accumulates across invocations.  Must be
    explicitly closed (or used with ``async with``).
    """

    def __init__(self, session: _ChatSession):
        self._session = session

    async def run(self, input: Any) -> str:
        parts: list[str] = []
        async for chunk in self._session.chat(str(input)):
            parts.append(chunk)
        return "".join(parts).strip()

    async def close(self) -> None:
        """Stop the underlying agent session."""
        await self._session.stop()

    async def __aenter__(self) -> "AgentRunnable":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def __repr__(self) -> str:
        name = getattr(self._session, "agent_id", "?")
        return f"<AgentRunnable {name}>"


class AgentFactory(BaseRunnable):
    """Ephemeral agent — creates a fresh session per call, destroys after.

    No conversation carry-over between calls.  No lifecycle management
    needed (each call is self-contained).
    """

    def __init__(self, config: AgentConfig | str | Path):
        self._config = config

    async def run(self, input: Any) -> str:
        session = await self._create_session()
        try:
            parts: list[str] = []
            async for chunk in session.chat(str(input)):
                parts.append(chunk)
            return "".join(parts).strip()
        finally:
            await session.stop()

    async def _create_session(self) -> _ChatSession:
        if isinstance(self._config, (str, Path)):
            return await _engine_session_from_path(str(self._config))
        return await _engine_session_from_config(self._config)

    def __repr__(self) -> str:
        if isinstance(self._config, AgentConfig):
            return f"<AgentFactory {self._config.name}>"
        return f"<AgentFactory {self._config}>"


# ── Convenience constructors ─────────────────────────────────────────


async def agent(config: AgentConfig | str | Path) -> AgentRunnable:
    """Create a persistent AgentRunnable (starts immediately).

    Usage::

        async with await agent("@kt-biome/creatures/swe") as a:
            result = await (a >> process)(task)
    """
    if isinstance(config, (str, Path)):
        session = await _engine_session_from_path(str(config))
    else:
        session = await _engine_session_from_config(config)
    return AgentRunnable(session)


def factory(config: AgentConfig | str | Path) -> AgentFactory:
    """Create an ephemeral AgentFactory (no startup cost).

    Each call to ``run()`` creates a fresh agent and destroys it after.

    Usage::

        specialist = factory(make_config("coder"))
        result = await specialist("Write a function that ...")
    """
    return AgentFactory(config)


# ── Engine-backed adapter ────────────────────────────────────────────


class _EngineChatSession:
    """Adapt a :class:`Terrarium` creature to the chat-session protocol.

    Owns the engine so each session is isolated; ``stop()`` shuts the
    engine down completely.  Used by the convenience constructors
    above so legacy ``compose`` callers don't need to know that the
    underlying runtime moved off ``AgentSession`` onto the engine.
    """

    def __init__(self, engine, creature) -> None:
        self._engine = engine
        self._creature = creature
        self.agent_id = creature.creature_id

    async def chat(self, message: str) -> AsyncIterator[str]:
        """Yield the creature's response one chunk at a time."""
        agent = self._creature.agent
        out_queue = _capture_output(agent)
        await agent.send_user_input(message)
        # Drain until the controller signals it's done with this turn.
        while True:
            chunk = await out_queue.get()
            if chunk is None:
                break
            yield chunk

    async def stop(self) -> None:
        await self._engine.shutdown()


def _capture_output(agent) -> "asyncio.Queue":
    """Hook the agent's output handler so we can stream chunks out."""
    queue: asyncio.Queue = asyncio.Queue()

    def _on_chunk(text: str) -> None:
        queue.put_nowait(text)

    agent.set_output_handler(_on_chunk)
    return queue


async def _engine_session_from_path(config_path: str) -> _EngineChatSession:
    engine = Terrarium()
    await engine.__aenter__()
    creature = await engine.add_creature(config_path)
    return _EngineChatSession(engine, creature)


async def _engine_session_from_config(config: AgentConfig) -> _EngineChatSession:
    engine = Terrarium()
    await engine.__aenter__()
    creature = await engine.add_creature(config)
    return _EngineChatSession(engine, creature)
