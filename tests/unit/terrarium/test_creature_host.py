"""Unit tests for :mod:`kohakuterrarium.terrarium.creature_host`."""

import asyncio


from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.testing.terrarium import _FakeAgent


def _creature(*, name="alice", agent=None, **kw):
    return Creature(
        creature_id=kw.pop("creature_id", name),
        name=name,
        agent=agent or _FakeAgent(name=name),
        **kw,
    )


# ── start / stop ───────────────────────────────────────────────


class TestStartStop:
    async def test_start_idempotent(self):
        c = _creature()
        await c.start()
        assert c._running
        await c.start()  # second call is no-op
        assert c._running

    async def test_stop_when_not_running(self):
        c = _creature()
        # No-op since not started.
        await c.stop()
        assert c._running is False

    async def test_start_then_stop(self):
        c = _creature()
        await c.start()
        await c.stop()
        assert c._running is False

    async def test_is_running_property(self):
        c = _creature()
        assert c.is_running is False
        await c.start()
        # _FakeAgent.is_running is also True after start.
        assert c.is_running is True

    async def test_drive_input_skipped_when_absent(self):
        # _FakeAgent has no _drive_input; start should not crash.
        c = _creature()
        await c.start()
        assert c._input_task is None


# ── inject_input ──────────────────────────────────────────────


class TestInjectInput:
    async def test_forwards_to_agent(self):
        agent = _FakeAgent(name="alice")
        c = _creature(agent=agent)
        await c.inject_input("hello")
        assert agent.injected[-1] == ("hello", "chat")


# ── chat streaming ────────────────────────────────────────────


class TestChat:
    async def test_streams_response_chunks(self):
        agent = _FakeAgent(name="alice", responses=["hi", " there"])
        c = _creature(agent=agent)
        chunks = []
        async for chunk in c.chat("ignored"):
            chunks.append(chunk)
        # The fake agent emits one full response chunk per injected input.
        assert "hi" in "".join(chunks)


# ── _ensure_chat_pipe / _on_output_chunk ──────────────────────


class TestPipe:
    def test_ensures_pipe_idempotent(self):
        c = _creature()
        c._ensure_chat_pipe()
        first_queue = c._output_queue
        c._ensure_chat_pipe()
        # Same queue, handler not re-installed.
        assert c._output_queue is first_queue

    def test_output_chunk_pushes_to_queue(self):
        c = _creature()
        c._ensure_chat_pipe()
        c._on_output_chunk("hi")
        # Queue has one item.
        assert c._output_queue.qsize() == 1

    def test_output_chunk_without_queue_silent(self):
        c = _creature()
        # No queue yet; chunk handler is a no-op.
        c._on_output_chunk("hi")
        assert c._output_queue is None


# ── status ────────────────────────────────────────────────────


class TestStatus:
    def test_get_status_basic(self):
        c = _creature()
        out = c.get_status()
        # Status reflects this creature's identity and (not-yet-started)
        # run state.
        assert out["creature_id"] == "alice"
        assert out["name"] == "alice"
        assert out["running"] is False


# ── _on_input_task_done ──────────────────────────────────────


class TestOnInputTaskDone:
    async def test_cancelled_marks_stopped(self):
        c = _creature()
        c._running = True

        async def cancelled_coro():
            await asyncio.sleep(100)

        task = asyncio.create_task(cancelled_coro())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        c._on_input_task_done(task)
        assert c._running is False

    async def test_success_marks_stopped(self):
        c = _creature()
        c._running = True

        async def ok():
            return None

        task = asyncio.create_task(ok())
        await task
        c._on_input_task_done(task)
        assert c._running is False

    async def test_exception_logged(self):
        c = _creature()
        c._running = True

        async def boom():
            raise RuntimeError("input boom")

        task = asyncio.create_task(boom())
        try:
            await task
        except RuntimeError:
            pass
        c._on_input_task_done(task)
        assert c._running is False


# ── _reap_input_task ─────────────────────────────────────────


class TestReapInputTask:
    async def test_no_task(self):
        c = _creature()
        # Returns silently.
        await c._reap_input_task()

    async def test_done_task(self):
        c = _creature()

        async def fast():
            return None

        c._input_task = asyncio.create_task(fast())
        await c._input_task
        # Done → reap is a no-op.
        await c._reap_input_task()
        assert c._input_task is None

    async def test_running_task_completes_within_timeout(self):
        c = _creature()

        async def quick():
            await asyncio.sleep(0.01)

        c._input_task = asyncio.create_task(quick())
        await c._reap_input_task()
        # Reaped.
        assert c._input_task is None
