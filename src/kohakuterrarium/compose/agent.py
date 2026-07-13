"""Agent wrappers — bridge between compose algebra and live agents.

Two modes:
- ``AgentRunnable``: persistent session, reused across calls
- ``AgentFactory``: ephemeral, creates a fresh agent per call

Convenience constructors:
- ``await agent(spec, *, engine=, pwd=, llm=)`` → AgentRunnable (started)
- ``factory(spec, *, engine=, pwd=, llm=)`` → AgentFactory (lazy)

``spec`` is anything the engine accepts: an :class:`AgentConfig`, a
filesystem path, or an ``@pkg/creatures/<name>`` package reference.
``llm`` follows the same grammar as ``Terrarium.add_creature`` — a
profile name, an :class:`LLMProfile`, or a provider instance.

When ``engine`` is omitted, each constructor stands up a private
:class:`Terrarium` that is shut down with the runnable; pass a shared
engine to amortize startup across many compose agents (closing the
runnable then only removes its creature, never your engine).

Each runnable accepts any object with the chat-session protocol:
``.chat(message) -> AsyncIterator[str]`` and ``async .stop()``.
"""

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


# ── Convenience constructors ─────────────────────────────────────────


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
    """Create a persistent AgentRunnable (starts immediately).

    Args:
        config: :class:`AgentConfig`, path, or ``@pkg/...`` reference.
        engine: Shared engine to spawn into; when ``None`` a private
            engine is created and torn down with the runnable.
        pwd: Working directory for the creature (no global chdir).
        llm: Profile name / :class:`LLMProfile` / provider instance.
        drive_config / drive_registrations / drive_store: Drive runtime
            overrides forwarded to the PRIVATE engine when ``engine`` is
            ``None`` (design §8.3); ignored when a shared engine is passed —
            that engine keeps its own Drive configuration.

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
    """Create an ephemeral AgentFactory (no startup cost).

    Each call to ``run()`` creates a fresh agent and destroys it after.
    Takes the same ``engine`` / ``pwd`` / ``llm`` and explicit Drive runtime
    keywords as :func:`agent` (the Drive args apply only to the private engine).

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


# ── Engine-backed adapter ────────────────────────────────────────────


class _EngineChatSession:
    """Adapt a :class:`Terrarium` creature to the chat-session protocol.

    ``owns_engine`` decides teardown: a private engine is shut down
    completely; a caller-shared engine only loses this creature.
    """

    def __init__(self, engine, creature, *, owns_engine: bool) -> None:
        self._engine = engine
        self._creature = creature
        self._owns_engine = owns_engine
        self.agent_id = creature.creature_id

    async def chat(self, message: str) -> AsyncIterator[str]:
        """Yield the creature's response one chunk at a time.

        Delegates to :meth:`Creature.chat` — the canonical
        inject-input + output-drain implementation.
        """
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
