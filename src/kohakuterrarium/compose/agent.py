"""Agent wrappers — bridge between compose algebra and AgentSession.

Two modes:
- ``AgentRunnable``: persistent session, reused across calls
- ``AgentFactory``: ephemeral, creates a fresh agent per call

Convenience constructors:
- ``await agent(config_or_path)`` → AgentRunnable (starts immediately)
- ``factory(config_or_path)`` → AgentFactory (lazy, no startup cost)
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.compose.core import BaseRunnable
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.serving.agent_session import AgentSession
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentRunnable(BaseRunnable):
    """Persistent agent — starts once, reused across calls.

    Conversation history accumulates across invocations.  Must be
    explicitly closed (or used with ``async with``).
    """

    def __init__(self, session: AgentSession):
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

    async def _create_session(self) -> AgentSession:
        if isinstance(self._config, (str, Path)):
            return await AgentSession.from_path(str(self._config))
        return await AgentSession.from_config(self._config)

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
        session = await AgentSession.from_path(str(config))
    else:
        session = await AgentSession.from_config(config)
    return AgentRunnable(session)


def factory(config: AgentConfig | str | Path) -> AgentFactory:
    """Create an ephemeral AgentFactory (no startup cost).

    Each call to ``run()`` creates a fresh agent and destroys it after.

    Usage::

        specialist = factory(make_config("coder"))
        result = await specialist("Write a function that ...")
    """
    return AgentFactory(config)
