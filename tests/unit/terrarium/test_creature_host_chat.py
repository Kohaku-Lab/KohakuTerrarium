"""Branch-coverage tests for :class:`Creature` chat-streaming and
input-task reaping — the concurrency arms ``test_creature_host_more``
doesn't reach.

``Creature.chat`` is a streaming generator: it drains an output queue
while the agent's ``inject_input`` runs as a background task, then
drains anything that landed after the inject completed. The reaping
helper guards a 5 s timeout with a cancel+await fallback.
"""

import asyncio

from kohakuterrarium.terrarium import creature_host as ch_mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.testing.terrarium import _FakeAgent

# ---------------------------------------------------------------------------
# Creature.chat — mid-stream + post-inject drain
# ---------------------------------------------------------------------------


class _StreamingAgent:
    """Agent stand-in whose ``inject_input`` runs long enough that the
    chat generator must loop on the timeout branch, and which pushes
    chunks both *during* and *after* the inject."""

    def __init__(self, *, post_inject_chunks=None, inject_delay=0.25):
        self.is_running = True
        self._handlers = []
        self._post = list(post_inject_chunks or [])
        self._delay = inject_delay

    def set_output_handler(self, handler, replace_default=False):
        self._handlers.append(handler)

    async def inject_input(self, message, *, source="chat"):
        # Emit one chunk immediately, then sleep past the chat
        # generator's 0.1 s wait_for timeout (forcing the continue arm),
        # then emit the post-inject chunks just before returning.
        for h in self._handlers:
            h("during")
        await asyncio.sleep(self._delay)
        for chunk in self._post:
            for h in self._handlers:
                h(chunk)


class TestChatStreaming:
    async def test_chat_loops_timeout_then_yields_during_and_after(self):
        """The generator yields the mid-inject chunk, survives the
        wait_for timeout, then drains the post-inject chunks."""
        agent = _StreamingAgent(post_inject_chunks=["after-1", "after-2"])
        c = Creature(creature_id="c", name="alice", agent=agent)
        chunks = [chunk async for chunk in c.chat("hi")]
        assert "during" in chunks
        assert "after-1" in chunks
        assert "after-2" in chunks

    async def test_chat_stops_on_none_sentinel_mid_stream(self):
        """A ``None`` pushed into the queue mid-stream ends the
        generator immediately — nothing after it is yielded."""

        class _SentinelAgent(_StreamingAgent):
            async def inject_input(self, message, *, source="chat"):
                for h in self._handlers:
                    h("first")
                await asyncio.sleep(0.15)
                # Push the stop sentinel while still running, then
                # idle long enough that the generator reads the ``None``
                # mid-loop and breaks (with an empty queue) before the
                # "never" chunk is ever enqueued.
                for h in self._handlers:
                    h(None)
                await asyncio.sleep(0.3)
                for h in self._handlers:
                    h("never")

        agent = _SentinelAgent()
        c = Creature(creature_id="c", name="alice", agent=agent)
        chunks = []
        async for chunk in c.chat("hi"):
            chunks.append(chunk)
        assert "first" in chunks
        assert "never" not in chunks

    async def test_chat_stale_drain_handles_empty_race(self):
        """The pre-turn stale-queue drain tolerates the queue going
        empty between ``empty()`` and ``get_nowait()`` — no crash."""
        agent = _FakeAgent(responses=["fresh"])
        c = Creature(creature_id="c", name="alice", agent=agent)
        c._ensure_chat_pipe()
        c._output_queue.put_nowait("stale")
        chunks = [chunk async for chunk in c.chat("hi")]
        # Stale chunk dropped, fresh response delivered.
        assert "stale" not in chunks
        assert "fresh" in chunks


# ---------------------------------------------------------------------------
# Creature._reap_input_task — timeout + cancellation fallback
# ---------------------------------------------------------------------------


class TestReapInputTaskTimeout:
    async def test_timeout_cancels_and_awaits_task(self, monkeypatch):
        """When the input task overruns the wait_for budget, it is
        cancelled and awaited — the task ends cancelled, _input_task
        cleared."""
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _forever():
            await asyncio.sleep(100)

        task = asyncio.create_task(_forever())
        c._input_task = task

        async def _fake_wait_for(awaitable, timeout):
            # Close the shield coroutine we were handed, then signal
            # timeout so the cancel-fallback arm runs.
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(ch_mod.asyncio, "wait_for", _fake_wait_for)
        await c._reap_input_task()
        assert c._input_task is None
        await asyncio.sleep(0)
        assert task.cancelled()

    async def test_wait_for_cancelled_is_swallowed(self, monkeypatch):
        """A ``CancelledError`` from ``wait_for`` itself (the shield
        case) is swallowed — reaping completes cleanly."""
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _quick():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(_quick())
        c._input_task = task

        async def _fake_wait_for(awaitable, timeout):
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.CancelledError

        monkeypatch.setattr(ch_mod.asyncio, "wait_for", _fake_wait_for)
        # Must not raise.
        await c._reap_input_task()
        assert c._input_task is None
        task.cancel()

    async def test_timeout_cancel_then_task_raises_is_logged(self, monkeypatch):
        """If the cancelled task surfaces a non-cancel exception while
        being awaited, it is logged and swallowed."""
        c = Creature(creature_id="c", name="alice", agent=_FakeAgent())

        async def _raises():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise RuntimeError("cleanup failed") from None

        task = asyncio.create_task(_raises())
        c._input_task = task

        async def _fake_wait_for(awaitable, timeout):
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(ch_mod.asyncio, "wait_for", _fake_wait_for)
        # Must not raise — the RuntimeError is caught + logged.
        await c._reap_input_task()
        assert c._input_task is None
