"""Unit tests for :mod:`kohakuterrarium.core.trigger_manager`."""

import asyncio
import time
from datetime import datetime

import pytest

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.trigger_manager import (
    SCHEDULE_DRIFT_THRESHOLD_S,
    TriggerInfo,
    TriggerManager,
)
from kohakuterrarium.modules.trigger.base import BaseTrigger

# ── stub triggers ─────────────────────────────────────────────────


class _StubTrigger(BaseTrigger):
    """Emits events from an asyncio.Queue."""

    def __init__(self):
        super().__init__()
        self.queue: asyncio.Queue[TriggerEvent | None] = asyncio.Queue()
        self.context_updates: list[dict] = []
        self.start_count = 0
        self.stop_count = 0

    async def _on_start(self) -> None:
        self.start_count += 1

    async def _on_stop(self) -> None:
        self.stop_count += 1

    async def wait_for_trigger(self) -> TriggerEvent | None:
        if not self._running:
            return None
        return await self.queue.get()

    def _on_context_update(self, context):
        self.context_updates.append(dict(context))


class _RaisingContextTrigger(_StubTrigger):
    def _on_context_update(self, context):
        raise RuntimeError("ctx update boom")


class _ResumableTrigger(_StubTrigger):
    resumable = True

    def to_resume_dict(self):
        return {"saved": True}


class _StubStore:
    def __init__(self):
        self.state_calls: list[dict] = []
        self.events: list[tuple] = []

    def save_state(self, agent_name, **kwargs):
        self.state_calls.append({"agent": agent_name, **kwargs})

    def append_event(self, agent_name, event_type, payload):
        self.events.append((agent_name, event_type, payload))


@pytest.fixture
def mgr():
    events: list[TriggerEvent] = []

    async def process(event):
        events.append(event)

    m = TriggerManager(process)
    m._collected = events  # accessible to tests
    yield m


# ── basic add / remove / list ─────────────────────────────────────


class TestAddRemoveList:
    async def test_add_autostart_starts_trigger(self, mgr):
        t = _StubTrigger()
        tid = await mgr.add(t)
        assert tid.startswith("trigger_")
        assert t.start_count == 1
        # Task is running.
        assert tid in mgr._tasks
        await mgr.remove(tid)
        assert t.stop_count == 1

    async def test_add_custom_id(self, mgr):
        await mgr.add(_StubTrigger(), trigger_id="my-id")
        assert "my-id" in mgr._triggers
        await mgr.remove("my-id")

    async def test_duplicate_id_rejected(self, mgr):
        await mgr.add(_StubTrigger(), trigger_id="dup")
        with pytest.raises(ValueError, match="already exists"):
            await mgr.add(_StubTrigger(), trigger_id="dup")
        await mgr.remove("dup")

    async def test_no_autostart_does_not_start(self, mgr):
        t = _StubTrigger()
        tid = await mgr.add(t, autostart=False)
        assert t.start_count == 0
        assert tid not in mgr._tasks
        await mgr.stop_all()

    async def test_remove_unknown_returns_false(self, mgr):
        assert await mgr.remove("nope") is False

    async def test_get_info_shape(self, mgr):
        tid = await mgr.add(_StubTrigger(), trigger_id="x")
        info = mgr.get(tid)
        assert isinstance(info, TriggerInfo)
        assert info.trigger_id == "x"
        assert info.trigger_type == "_StubTrigger"
        assert info.running is True
        assert isinstance(info.created_at, datetime)
        await mgr.remove(tid)

    async def test_get_unknown_returns_none(self, mgr):
        assert mgr.get("nope") is None

    async def test_list(self, mgr):
        a = await mgr.add(_StubTrigger())
        b = await mgr.add(_StubTrigger())
        ids = {i.trigger_id for i in mgr.list()}
        assert ids == {a, b}
        await mgr.stop_all()

    async def test_get_trigger(self, mgr):
        t = _StubTrigger()
        tid = await mgr.add(t)
        assert mgr.get_trigger(tid) is t
        assert mgr.get_trigger("nope") is None
        await mgr.remove(tid)


# ── event loop wiring ─────────────────────────────────────────────


class TestEventLoop:
    async def test_event_dispatched_to_process_callback(self, mgr):
        t = _StubTrigger()
        tid = await mgr.add(t)
        evt = TriggerEvent(type="timer", content="hi")
        await t.queue.put(evt)
        # Let the loop pick it up.
        for _ in range(50):
            if mgr._collected:
                break
            await asyncio.sleep(0.01)
        assert mgr._collected == [evt]
        await mgr.remove(tid)

    async def test_on_trigger_fired_callback(self, mgr):
        seen: list[tuple] = []
        mgr.on_trigger_fired = lambda tid, evt: seen.append((tid, evt))
        t = _StubTrigger()
        tid = await mgr.add(t, trigger_id="x")
        await t.queue.put(TriggerEvent(type="timer"))
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen[0][0] == "x"
        await mgr.remove(tid)

    async def test_callback_exception_is_swallowed(self, mgr):
        def explode(_tid, _evt):
            raise RuntimeError("nope")

        mgr.on_trigger_fired = explode
        t = _StubTrigger()
        tid = await mgr.add(t)
        await t.queue.put(TriggerEvent(type="timer"))
        # Event still reaches process_event despite callback raising.
        for _ in range(50):
            if mgr._collected:
                break
            await asyncio.sleep(0.01)
        assert mgr._collected
        await mgr.remove(tid)

    async def test_none_event_skipped(self, mgr):
        t = _StubTrigger()
        tid = await mgr.add(t)
        await t.queue.put(None)
        await t.queue.put(TriggerEvent(type="timer", content="real"))
        for _ in range(50):
            if mgr._collected:
                break
            await asyncio.sleep(0.01)
        # Only the real event made it.
        assert len(mgr._collected) == 1
        await mgr.remove(tid)


# ── start_all / stop_all ──────────────────────────────────────────


class TestStartStopAll:
    async def test_start_all_starts_pending(self, mgr):
        t1 = _StubTrigger()
        t2 = _StubTrigger()
        await mgr.add(t1, trigger_id="a", autostart=False)
        await mgr.add(t2, trigger_id="b", autostart=False)
        assert t1.start_count == 0
        await mgr.start_all()
        assert t1.start_count == 1
        assert t2.start_count == 1
        await mgr.stop_all()

    async def test_start_all_skips_already_started(self, mgr):
        t = _StubTrigger()
        await mgr.add(t)  # already started
        await mgr.start_all()
        # Still only started once.
        assert t.start_count == 1
        await mgr.stop_all()

    async def test_stop_all_clears(self, mgr):
        await mgr.add(_StubTrigger())
        await mgr.add(_StubTrigger())
        await mgr.stop_all()
        assert mgr._triggers == {}
        assert mgr._tasks == {}
        assert mgr._created_at == {}


# ── set_context_all ───────────────────────────────────────────────


class TestSetContextAll:
    async def test_distributes_context(self, mgr):
        t1 = _StubTrigger()
        t2 = _StubTrigger()
        await mgr.add(t1)
        await mgr.add(t2)
        mgr.set_context_all({"k": "v"})
        assert t1.context_updates == [{"k": "v"}]
        assert t2.context_updates == [{"k": "v"}]
        await mgr.stop_all()

    async def test_swallow_per_trigger_failures(self, mgr):
        bad = _RaisingContextTrigger()
        good = _StubTrigger()
        await mgr.add(bad)
        await mgr.add(good)
        # Should not raise — bad trigger's exception is swallowed,
        # good trigger still receives the update.
        mgr.set_context_all({"k": "v"})
        assert good.context_updates == [{"k": "v"}]
        await mgr.stop_all()


# ── resumable persistence ─────────────────────────────────────────


class TestResumablePersistence:
    async def test_resumable_trigger_persisted(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        mgr._agent_name = "a1"
        await mgr.add(_ResumableTrigger(), trigger_id="r1")
        assert store.state_calls, "save_state should have been called"
        saved = store.state_calls[-1]
        assert saved["agent"] == "a1"
        trig_entries = saved["triggers"]
        assert trig_entries[0]["trigger_id"] == "r1"
        assert trig_entries[0]["type"] == "_ResumableTrigger"
        assert trig_entries[0]["data"] == {"saved": True}
        await mgr.remove("r1")

    async def test_non_resumable_not_persisted(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        await mgr.add(_StubTrigger())
        assert store.state_calls == []
        await mgr.stop_all()


# ── schedule_drift observability ──────────────────────────────────


class TestScheduleDrift:
    async def test_drift_above_threshold_emits_event(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        mgr._agent_name = "a1"
        t = _StubTrigger()
        tid = await mgr.add(t)
        scheduled = time.time() - (SCHEDULE_DRIFT_THRESHOLD_S + 5.0)
        evt = TriggerEvent(type="timer")
        # ``scheduled_at`` is read via ``getattr(event, ...)`` first; the
        # event dataclass doesn't declare the field but the trigger
        # manager tolerates attribute injection on the instance.
        object.__setattr__(evt, "scheduled_at", scheduled)
        await t.queue.put(evt)
        for _ in range(50):
            if store.events:
                break
            await asyncio.sleep(0.01)
        assert store.events
        agent, etype, payload = store.events[0]
        assert agent == "a1"
        assert etype == "schedule_drift"
        assert payload["trigger_id"] == tid
        assert payload["drift_ms"] >= SCHEDULE_DRIFT_THRESHOLD_S * 1000
        await mgr.remove(tid)

    async def test_drift_under_threshold_silent(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        t = _StubTrigger()
        tid = await mgr.add(t)
        evt = TriggerEvent(type="timer")
        object.__setattr__(evt, "scheduled_at", time.time())
        await t.queue.put(evt)
        # Let the loop run.
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert store.events == []
        await mgr.remove(tid)

    async def test_no_scheduled_at_no_emit(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        t = _StubTrigger()
        tid = await mgr.add(t)
        await t.queue.put(TriggerEvent(type="timer"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert store.events == []
        await mgr.remove(tid)

    async def test_scheduled_in_context(self, mgr):
        store = _StubStore()
        mgr._session_store = store
        t = _StubTrigger()
        tid = await mgr.add(t)
        scheduled = time.time() - (SCHEDULE_DRIFT_THRESHOLD_S + 2.0)
        evt = TriggerEvent(
            type="timer",
            context={"scheduled_at": scheduled},
        )
        await t.queue.put(evt)
        for _ in range(50):
            if store.events:
                break
            await asyncio.sleep(0.01)
        assert store.events
        await mgr.remove(tid)

    async def test_no_store_no_emit(self, mgr):
        # No store attached → nothing to write to, but must not crash.
        t = _StubTrigger()
        tid = await mgr.add(t)
        scheduled = time.time() - 10.0
        evt = TriggerEvent(type="timer")
        object.__setattr__(evt, "scheduled_at", scheduled)
        await t.queue.put(evt)
        for _ in range(20):
            await asyncio.sleep(0.01)
        await mgr.remove(tid)


class TestResumablePersistFailure:
    async def test_save_state_failure_swallowed(self, mgr):
        """Exception inside save_state during a resumable trigger add is
        logged but doesn't break the registration (lines 115-116)."""
        store = _StubStore()

        def boom(*a, **kw):
            raise RuntimeError("disk")

        store.save_state = boom
        mgr._session_store = store
        mgr._agent_name = "a1"
        await mgr.add(_ResumableTrigger(), trigger_id="r1")
        # Trigger still registered.
        assert "r1" in mgr._triggers
        await mgr.remove("r1")


class TestRemoveCancellationError:
    async def test_task_cleanup_unexpected_exception(self, mgr):
        """A non-CancelledError exception while joining the cancelled
        task is logged via the ``except Exception`` arm (lines 143-144)."""
        t = _StubTrigger()
        tid = await mgr.add(t)

        # Stop the original task and replace with one that converts
        # CancelledError into a RuntimeError during cleanup.
        original_task = mgr._tasks[tid]
        original_task.cancel()
        try:
            await original_task
        except (asyncio.CancelledError, Exception):
            pass

        async def _converts_cancel():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise RuntimeError("cleanup boom") from None

        wrapped = asyncio.create_task(_converts_cancel())
        mgr._tasks[tid] = wrapped
        # remove() calls task.cancel() + await task — the inner
        # coroutine swallows CancelledError and raises RuntimeError,
        # which hits the ``except Exception`` log arm (143-144).
        ok = await mgr.remove(tid)
        assert ok is True


class TestRunLoopGenericException:
    async def test_wait_for_trigger_raises_generic_exception(self, mgr):
        """Generic exception from wait_for_trigger triggers the
        ``except Exception`` log path (lines 246-252) — but the loop
        retries after a short sleep. We test by raising once."""

        class _FaultyTrigger(_StubTrigger):
            def __init__(self):
                super().__init__()
                self.raised = False

            async def wait_for_trigger(self):
                if not self.raised:
                    self.raised = True
                    raise RuntimeError("trigger boom")
                # Subsequent calls block.
                return await self.queue.get()

        t = _FaultyTrigger()
        tid = await mgr.add(t)
        # Wait for the error path to fire.
        for _ in range(150):  # up to ~1.5s
            if t.raised:
                break
            await asyncio.sleep(0.01)
        assert t.raised
        await mgr.remove(tid)
