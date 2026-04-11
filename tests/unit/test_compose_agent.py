"""Integration tests for compose agent wrappers (mock AgentSession)."""

from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.compose import AgentFactory, AgentRunnable

# ── Mock AgentSession ────────────────────────────────────────────────


class MockAgentSession:
    """Minimal mock that implements the AgentSession interface."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._call_count = 0
        self.agent_id = "mock-agent"
        self.stopped = False

    async def chat(self, message: str) -> AsyncIterator[str]:
        idx = min(self._call_count, len(self._responses) - 1)
        response = self._responses[idx]
        self._call_count += 1
        # Simulate streaming by yielding word-by-word
        for word in response.split(" "):
            yield word + " "

    async def stop(self) -> None:
        self.stopped = True


# ── AgentRunnable ────────────────────────────────────────────────────


class TestAgentRunnable:
    @pytest.mark.asyncio
    async def test_basic_chat(self):
        session = MockAgentSession(["Hello world"])
        runnable = AgentRunnable(session)
        result = await runnable.run("hi")
        assert "Hello" in result
        assert "world" in result

    @pytest.mark.asyncio
    async def test_preserves_history(self):
        session = MockAgentSession(["First", "Second", "Third"])
        runnable = AgentRunnable(session)
        r1 = await runnable.run("msg1")
        r2 = await runnable.run("msg2")
        r3 = await runnable.run("msg3")
        assert "First" in r1
        assert "Second" in r2
        assert "Third" in r3

    @pytest.mark.asyncio
    async def test_call_sugar(self):
        session = MockAgentSession(["Response"])
        runnable = AgentRunnable(session)
        result = await runnable("prompt")
        assert "Response" in result

    @pytest.mark.asyncio
    async def test_close(self):
        session = MockAgentSession(["ok"])
        runnable = AgentRunnable(session)
        await runnable.close()
        assert session.stopped

    @pytest.mark.asyncio
    async def test_context_manager(self):
        session = MockAgentSession(["ok"])
        async with AgentRunnable(session) as runnable:
            result = await runnable("test")
            assert "ok" in result
        assert session.stopped

    @pytest.mark.asyncio
    async def test_sequence_with_pure(self):
        session = MockAgentSession(["The answer is 42"])
        runnable = AgentRunnable(session)
        pipeline = runnable >> (lambda s: s.upper())
        result = await pipeline.run("question")
        assert "THE ANSWER IS 42" in result

    @pytest.mark.asyncio
    async def test_parallel_agents(self):
        s1 = MockAgentSession(["Alpha"])
        s2 = MockAgentSession(["Beta"])
        pipeline = AgentRunnable(s1) & AgentRunnable(s2)
        r1, r2 = await pipeline.run("same input")
        assert "Alpha" in r1
        assert "Beta" in r2

    @pytest.mark.asyncio
    async def test_iterate(self):
        session = MockAgentSession(["Round 1", "Round 2", "APPROVED"])
        runnable = AgentRunnable(session)
        results = []
        async for result in runnable.iterate("start"):
            results.append(result)
            if "APPROVED" in result:
                break
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_repr(self):
        session = MockAgentSession(["ok"])
        runnable = AgentRunnable(session)
        assert "AgentRunnable" in repr(runnable)


# ── AgentFactory ─────────────────────────────────────────────────────


class TestAgentFactory:
    @pytest.mark.asyncio
    async def test_creates_fresh_each_call(self):
        """Factory creates a new session per invocation."""
        sessions: list[MockAgentSession] = []
        call_count = 0

        class TrackingFactory(AgentFactory):
            async def _create_session(self_inner):
                nonlocal call_count
                call_count += 1
                s = MockAgentSession([f"Response {call_count}"])
                sessions.append(s)
                return s

        f = TrackingFactory("fake-config")
        r1 = await f.run("task 1")
        r2 = await f.run("task 2")

        assert call_count == 2
        assert "Response 1" in r1
        assert "Response 2" in r2
        assert all(s.stopped for s in sessions)

    @pytest.mark.asyncio
    async def test_repr_with_config(self):
        config = MagicMock()
        config.name = "test-agent"
        f = AgentFactory(config)
        assert "AgentFactory" in repr(f)

    @pytest.mark.asyncio
    async def test_repr_with_path(self):
        f = AgentFactory("@kt-defaults/creatures/swe")
        assert "AgentFactory" in repr(f)
        assert "kt-defaults" in repr(f)


# ── Compose patterns with agents ─────────────────────────────────────


class TestComposePatterns:
    @pytest.mark.asyncio
    async def test_agent_sequence_agent(self):
        """Two agents in sequence (writer >> reviewer pattern)."""
        writer = MockAgentSession(["Here is my code: print('hello')"])
        reviewer = MockAgentSession(["LGTM, code looks good"])

        pipeline = AgentRunnable(writer) >> AgentRunnable(reviewer)
        result = await pipeline.run("Write hello world")
        assert "LGTM" in result

    @pytest.mark.asyncio
    async def test_agent_fallback(self):
        """Expert fails, generalist succeeds."""

        class FailSession(MockAgentSession):
            async def chat(self, message):
                raise RuntimeError("Expert unavailable")
                yield  # make it a generator

        expert = AgentRunnable(FailSession([]))
        generalist = AgentRunnable(MockAgentSession(["Generalist response"]))

        pipeline = expert | generalist
        result = await pipeline.run("help")
        assert "Generalist" in result

    @pytest.mark.asyncio
    async def test_agent_with_transform(self):
        """Agent output transformed before next step."""
        session = MockAgentSession(["  HELLO WORLD  "])
        pipeline = AgentRunnable(session) >> str.strip >> str.lower
        result = await pipeline.run("test")
        assert result == "hello world"
