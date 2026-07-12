"""Unit tests for :mod:`kohakuterrarium.compose.agent`.

These tests substitute the underlying engine session with a scripted
fake so the compose-side behavior is verifiable without a live
Terrarium engine.
"""

import importlib
from pathlib import Path
from typing import AsyncIterator

import pytest

from kohakuterrarium.compose.agent import (
    AgentFactory,
    AgentRunnable,
    agent,
    factory,
)
from kohakuterrarium.core.config_types import AgentConfig

# ``compose/__init__.py`` does ``from .agent import agent``, so the
# attribute ``kohakuterrarium.compose.agent`` is the *function*, not
# the submodule — ``import ... as`` would bind the function. Use
# ``importlib.import_module`` to get the actual module object (needed
# for monkeypatching its ``_engine_session`` helper).
compose_agent = importlib.import_module("kohakuterrarium.compose.agent")


# ── fake session ──────────────────────────────────────────────────


class _FakeSession:
    """Conforms to the ``_ChatSession`` protocol."""

    def __init__(self, *, agent_id="fake", chunks=None):
        self.agent_id = agent_id
        self._chunks = chunks or ["hello ", "world"]
        self.stop_count = 0

    async def chat(self, message: str) -> AsyncIterator[str]:
        for c in self._chunks:
            yield c

    async def stop(self) -> None:
        self.stop_count += 1


def _stub_engine_session(monkeypatch, sess, created=None):
    """Patch ``_engine_session`` to return ``sess`` and log its args."""

    async def fake_session(config, *, engine, pwd, llm, drive=(None, (), None)):
        if created is not None:
            created.append(
                {
                    "config": config,
                    "engine": engine,
                    "pwd": pwd,
                    "llm": llm,
                    "drive": drive,
                }
            )
        return sess

    monkeypatch.setattr(compose_agent, "_engine_session", fake_session)


# ── AgentRunnable ────────────────────────────────────────────────


class TestAgentRunnable:
    async def test_run_concatenates_chunks(self):
        sess = _FakeSession(chunks=["a", "b", "c"])
        a = AgentRunnable(sess)
        out = await a.run("hi")
        assert out == "abc"

    async def test_run_strips_whitespace(self):
        sess = _FakeSession(chunks=["  spaced  "])
        a = AgentRunnable(sess)
        assert await a.run("x") == "spaced"

    async def test_close_stops_session(self):
        sess = _FakeSession()
        a = AgentRunnable(sess)
        await a.close()
        assert sess.stop_count == 1

    async def test_async_context_manager(self):
        sess = _FakeSession()
        runnable = AgentRunnable(sess)
        async with runnable as a:
            # ``__aenter__`` yields the same runnable, not a copy.
            assert a is runnable
        # Context exit calls close → stop.
        assert sess.stop_count == 1

    def test_repr_includes_agent_id(self):
        sess = _FakeSession(agent_id="alpha-creature-1")
        a = AgentRunnable(sess)
        assert "alpha-creature-1" in repr(a)

    async def test_input_coerced_to_str(self):
        # AgentRunnable always passes ``str(input)`` to chat.
        sess = _FakeSession(chunks=["ok"])
        a = AgentRunnable(sess)
        out = await a.run(42)
        assert out == "ok"


# ── AgentFactory ─────────────────────────────────────────────────


class TestAgentFactory:
    async def test_run_creates_destroys_session(self, monkeypatch):
        sess = _FakeSession(chunks=["fresh-"])
        created = []
        _stub_engine_session(monkeypatch, sess, created)
        f = AgentFactory("/some/path")
        out = await f.run("task")
        assert out == "fresh-"
        assert [c["config"] for c in created] == ["/some/path"]
        # Session stopped after the call.
        assert sess.stop_count == 1

    async def test_run_with_agent_config(self, monkeypatch):
        sess = _FakeSession(chunks=["x"])
        created = []
        _stub_engine_session(monkeypatch, sess, created)
        cfg = AgentConfig(name="c", agent_path=Path("."))
        f = AgentFactory(cfg)
        out = await f.run("task")
        assert out == "x"
        assert created[0]["config"] is cfg

    async def test_engine_pwd_llm_threaded_through(self, monkeypatch):
        # E11: the constructor keywords reach the engine adapter
        # verbatim on EVERY run() — they used to not exist at all.
        sess = _FakeSession(chunks=["y"])
        created = []
        _stub_engine_session(monkeypatch, sess, created)
        shared_engine = object()
        provider = object()
        f = AgentFactory(
            "@pkg/creatures/x", engine=shared_engine, pwd="/work", llm=provider
        )
        await f.run("task")
        assert created == [
            {
                "config": "@pkg/creatures/x",
                "engine": shared_engine,
                "pwd": "/work",
                "llm": provider,
                "drive": (None, (), None),
            }
        ]

    async def test_session_stopped_even_on_exception(self, monkeypatch):
        class _BadSession(_FakeSession):
            async def chat(self, message):
                if False:
                    yield ""
                raise RuntimeError("chat boom")

        sess = _BadSession()
        _stub_engine_session(monkeypatch, sess)
        f = AgentFactory("p")
        with pytest.raises(RuntimeError, match="chat boom"):
            await f.run("x")
        # Session was still stopped in finally.
        assert sess.stop_count == 1

    def test_repr_with_config_object(self):
        cfg = AgentConfig(name="my-cfg", agent_path=Path("."))
        f = AgentFactory(cfg)
        assert "my-cfg" in repr(f)

    def test_repr_with_path(self):
        f = AgentFactory("/some/path")
        assert "/some/path" in repr(f)


# ── convenience constructors ─────────────────────────────────────


class TestAgentHelper:
    async def test_agent_from_path(self, monkeypatch):
        sess = _FakeSession(chunks=["from-path"])
        created = []
        _stub_engine_session(monkeypatch, sess, created)
        a = await agent("/x")
        # The runnable wraps the session built from the path, and
        # running it streams through that session.
        assert a._session is sess
        assert created[0]["config"] == "/x"
        assert await a.run("hi") == "from-path"

    async def test_agent_from_config(self, monkeypatch):
        sess = _FakeSession(chunks=["from-config"])
        _stub_engine_session(monkeypatch, sess)
        cfg = AgentConfig(name="c", agent_path=Path("."))
        a = await agent(cfg)
        assert a._session is sess
        assert await a.run("hi") == "from-config"

    async def test_agent_keywords_threaded_through(self, monkeypatch):
        sess = _FakeSession()
        created = []
        _stub_engine_session(monkeypatch, sess, created)
        shared_engine = object()
        await agent("/x", engine=shared_engine, pwd="/w", llm="profile-name")
        assert created[0]["engine"] is shared_engine
        assert created[0]["pwd"] == "/w"
        assert created[0]["llm"] == "profile-name"


class TestFactoryHelper:
    def test_factory_wraps_the_given_source(self):
        f = factory("/x")
        # ``factory`` returns a lazy AgentFactory bound to the source —
        # no session created yet, but the source is recorded.
        assert isinstance(f, AgentFactory)
        assert "/x" in repr(f)


# ── _EngineChatSession behaviour ─────────────────────────────────


class _FakeCreature:
    creature_id = "cid"

    def __init__(self):
        self.received: list[str] = []

    async def chat(self, message):
        # Mirror Creature.chat's real shape: an async generator
        # that yields the response chunks and then simply ends
        # (no caller-visible sentinel).
        self.received.append(message)
        yield "part-1"
        yield "part-2"


class _FakeEngine:
    def __init__(self):
        self.shut = False
        self.removed: list[str] = []

    async def shutdown(self):
        self.shut = True

    async def remove_creature(self, creature_id):
        self.removed.append(creature_id)


class TestEngineChatSession:
    async def test_chat_delegates_to_creature_chat(self):
        """Contract: ``_EngineChatSession.chat`` adapts a Terrarium
        creature into the chat-session protocol by delegating to the
        creature's own ``chat`` — the canonical inject + output-drain
        implementation. (Regression guard for B-compose-1: the prior
        version called a non-existent ``agent.send_user_input``.)"""
        from kohakuterrarium.compose.agent import _EngineChatSession

        creature = _FakeCreature()
        engine = _FakeEngine()
        sess = _EngineChatSession(engine, creature, owns_engine=True)

        assert sess.agent_id == "cid"
        chunks = [c async for c in sess.chat("hi")]
        # The message reached the creature, and every yielded chunk
        # surfaced — the iteration ends naturally, no hang.
        assert creature.received == ["hi"]
        assert chunks == ["part-1", "part-2"]

        await sess.stop()
        assert engine.shut is True

    async def test_shared_engine_survives_stop(self):
        """E11: with a caller-shared engine, ``stop()`` removes ONLY
        this session's creature — shutting down the user's engine
        would kill every other compose agent riding on it."""
        from kohakuterrarium.compose.agent import _EngineChatSession

        creature = _FakeCreature()
        engine = _FakeEngine()
        sess = _EngineChatSession(engine, creature, owns_engine=False)

        await sess.stop()
        assert engine.shut is False
        assert engine.removed == ["cid"]
