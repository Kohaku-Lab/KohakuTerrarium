"""Agent wrappers — bridge between compose algebra and live agents.

Composable adapters for persistent and per-call agent sessions.

Specs may be configs, paths, or package references. Without a supplied engine,
the adapter owns a private :class:`Terrarium`; with a shared engine, closing the
adapter removes only its creature.
"""

from pathlib import Path
from typing import Any, AsyncIterator, Protocol

from kohakuterrarium.compose.core import BaseRunnable
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class _ChatSession(Protocol):
    """Chat and lifecycle surface required by compose agent adapters."""

    agent_id: str

    def chat(self, message: str) -> AsyncIterator[str]: ...

    async def stop(self) -> None: ...


class AgentRunnable(BaseRunnable):
    """Persistent agent whose conversation state carries across calls."""

    def __init__(self, session: _ChatSession):
        self._session = session

    async def run(self, input: Any) -> str:
        parts: list[str] = []
        async for chunk in self._session.chat(str(input)):
            parts.append(chunk)
        return "".join(parts).strip()

    async def close(self) -> None:
        """Stop the underlying session and release its engine resources."""
        await self._session.stop()

    async def __aenter__(self) -> "AgentRunnable":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def __repr__(self) -> str:
        name = getattr(self._session, "agent_id", "?")
        return f"<AgentRunnable {name}>"


class AgentFactory(BaseRunnable):
    """Create and destroy an isolated agent session for every call."""

    def __init__(
        self,
        config: AgentConfig | str | Path,
        *,
        engine: Terrarium | None = None,
        pwd: str | Path | None = None,
        llm: Any = None,
        drive_config: Any = None,
        drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
        drive_store: Any = None,
    ):
        self._config = config
        self._engine = engine
        self._pwd = pwd
        self._llm = llm
        self._drive = (
            drive_config,
            None if drive_registrations is None else tuple(drive_registrations),
            drive_store,
        )

    async def run(self, input: Any) -> str:
        session = await _engine_session(
            self._config,
            engine=self._engine,
            pwd=self._pwd,
            llm=self._llm,
            drive=self._drive,
        )
        try:
            parts: list[str] = []
            async for chunk in session.chat(str(input)):
                parts.append(chunk)
            return "".join(parts).strip()
        finally:
            await session.stop()

    def __repr__(self) -> str:
        if isinstance(self._config, AgentConfig):
            return f"<AgentFactory {self._config.name}>"
        return f"<AgentFactory {self._config}>"


# Public constructors.


async def agent(
    config: AgentConfig | str | Path,
    *,
    engine: Terrarium | None = None,
    pwd: str | Path | None = None,
    llm: Any = None,
    drive_config: Any = None,
    drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
    drive_store: Any = None,
) -> AgentRunnable:
    """Create and start a persistent :class:`AgentRunnable`.

    Args:
        config: Agent config, path, or package reference.
        engine: Shared engine, or ``None`` to create an owned private engine.
        pwd: Creature working directory without changing process state.
        llm: Profile name, profile object, or provider instance.
        drive_config: Drive configuration for a private engine.
        drive_registrations: Drive registrations for a private engine.
        drive_store: Drive store for a private engine.

    Usage::

        async with await agent("@kt-biome/creatures/swe", llm="fast") as a:
            result = await (a >> process)(task)
    """
    session = await _engine_session(
        config,
        engine=engine,
        pwd=pwd,
        llm=llm,
        drive=(
            drive_config,
            None if drive_registrations is None else tuple(drive_registrations),
            drive_store,
        ),
    )
    return AgentRunnable(session)


def factory(
    config: AgentConfig | str | Path,
    *,
    engine: Terrarium | None = None,
    pwd: str | Path | None = None,
    llm: Any = None,
    drive_config: Any = None,
    drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
    drive_store: Any = None,
) -> AgentFactory:
    """Create a lazy factory that uses a fresh agent for each call.

    Drive arguments apply only when the factory owns its engine.

    Usage::

        specialist = factory(make_config("coder"), llm=my_provider)
        result = await specialist("Write a function that ...")
    """
    return AgentFactory(
        config,
        engine=engine,
        pwd=pwd,
        llm=llm,
        drive_config=drive_config,
        drive_registrations=drive_registrations,
        drive_store=drive_store,
    )


# Engine-backed session adapter.


class _EngineChatSession:
    """Adapt a creature while preserving private versus shared engine ownership."""

    def __init__(self, engine, creature, *, owns_engine: bool) -> None:
        self._engine = engine
        self._creature = creature
        self._owns_engine = owns_engine
        self.agent_id = creature.creature_id

    async def chat(self, message: str) -> AsyncIterator[str]:
        """Yield response chunks through the creature's canonical chat path."""
        async for chunk in self._creature.chat(message):
            yield chunk

    async def stop(self) -> None:
        if self._owns_engine:
            await self._engine.shutdown()
        else:
            await self._engine.remove_creature(self._creature.creature_id)


async def _engine_session(
    config: AgentConfig | str | Path,
    *,
    engine: Terrarium | None,
    pwd: str | Path | None,
    llm: Any,
    drive: "tuple[Any, tuple[Any, ...] | None, Any]" = (None, None, None),
) -> _EngineChatSession:
    owns_engine = engine is None
    if owns_engine:
        drive_config, drive_registrations, drive_store = drive
        engine = Terrarium(
            drive_config=drive_config,
            drive_registrations=drive_registrations,
            drive_store=drive_store,
        )
        await engine.__aenter__()
    try:
        spec = str(config) if isinstance(config, (str, Path)) else config
        creature = await engine.add_creature(spec, pwd=pwd, llm=llm)
    except BaseException:
        if owns_engine:
            await engine.shutdown()
        raise
    return _EngineChatSession(engine, creature, owns_engine=owns_engine)
