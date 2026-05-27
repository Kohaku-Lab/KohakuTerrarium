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


# ── status enum (Creature.status) ────────────────────────────


class TestCreatureStatusEnum:
    """The ``status`` property replaces the broken ``running: bool``
    view used by ``group_status``. It must distinguish five lifecycle
    states; the old bool collapsed all of them into ``False`` once the
    input loop exited even on clean stop.
    """

    def test_not_started_before_first_start(self):
        c = _creature()
        # Constructed but ``start()`` has never run.
        assert c.status == "not_started"

    async def test_idle_after_start(self):
        c = _creature()
        await c.start()
        # Alive, no in-flight processing task.
        assert c.status == "idle"

    async def test_busy_when_processing_task_present(self):
        """A live ``Agent._processing_task`` should surface as ``busy``."""
        c = _creature()
        await c.start()

        async def loop_body():
            await asyncio.sleep(0.5)

        c.agent._processing_task = asyncio.create_task(loop_body())
        try:
            assert c.status == "busy"
        finally:
            c.agent._processing_task.cancel()
            try:
                await c.agent._processing_task
            except asyncio.CancelledError:
                pass
            c.agent._processing_task = None

    async def test_busy_clears_back_to_idle_when_task_done(self):
        """``_processing_task.done()`` → idle again, even if the task
        attribute hasn't been cleared yet (it's only nulled in the
        ``finally`` block of ``_process_event_with_controller``)."""
        c = _creature()
        await c.start()

        async def already_done():
            return None

        task = asyncio.create_task(already_done())
        await task
        c.agent._processing_task = task
        # Task is done — status must read idle, not busy.
        assert c.status == "idle"

    async def test_stopped_after_stop(self):
        c = _creature()
        await c.start()
        await c.stop()
        assert c.status == "stopped"

    async def test_stopped_distinct_from_not_started(self):
        """The whole point of the new enum: stopped ≠ not_started.
        The old ``running: False`` could mean either."""
        fresh = _creature(name="fresh")
        cycled = _creature(name="cycled")
        await cycled.start()
        await cycled.stop()
        assert fresh.status == "not_started"
        assert cycled.status == "stopped"
        # They must be observably different.
        assert fresh.status != cycled.status

    async def test_error_when_input_loop_crashes(self):
        c = _creature()
        await c.start()

        async def boom():
            raise RuntimeError("input crash")

        task = asyncio.create_task(boom())
        try:
            await task
        except RuntimeError:
            pass
        c._on_input_task_done(task)
        # The agent itself may still report alive — the error state is
        # carried on the creature wrapper, not the agent.
        assert c.status == "error"

    async def test_restart_clears_prior_error(self):
        """A fresh ``start()`` must wipe stale error state — otherwise
        a creature that crashed once would be permanently un-recoverable."""
        c = _creature()
        await c.start()
        c._input_loop_error = RuntimeError("ancient history")
        # While alive but flagged as error, status should reflect error.
        assert c.status == "error"
        await c.stop()
        await c.start()
        # Error cleared, agent alive again.
        assert c._input_loop_error is None
        assert c.status == "idle"


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
        # The exception must be captured on the creature so ``status``
        # can surface it. A bare ``_running = False`` flip silently
        # discarded the error before — that's the regression this
        # assertion pins.
        assert isinstance(c._input_loop_error, RuntimeError)

    async def test_cancelled_does_not_record_error(self):
        """A clean cancel must NOT poison ``_input_loop_error`` —
        otherwise ``stop()`` would leave the creature reading "error"
        forever on the next ``status`` query."""
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
        assert c._input_loop_error is None


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
