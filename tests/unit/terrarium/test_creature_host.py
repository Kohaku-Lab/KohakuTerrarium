"""Unit tests for :mod:`kohakuterrarium.terrarium.creature_host`."""

import asyncio

import pytest

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.builtins.outputs.stdout import StdoutOutput
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.terrarium.creature_host import Creature, build_creature
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.terrarium import _FakeAgent


def _creature(*, name="alice", agent=None, **kw):
    return Creature(
        creature_id=kw.pop("creature_id", name),
        name=name,
        agent=agent or _FakeAgent(name=name),
        **kw,
    )


# ── build_creature: llm= instance injection (E5) ───────────────


class TestBuildCreatureLLMInjection:
    def test_provider_instance_flows_to_agent(self, tmp_path):
        # ``engine.add_creature(path, llm=ScriptedLLM(...))`` must bind
        # the instance — this is the engine-side seam that replaces the
        # old two-site create_llm_provider monkeypatch.
        (tmp_path / "config.yaml").write_text(
            "name: scripted\ninput:\n  type: none\noutput:\n  type: stdout\n",
            encoding="utf-8",
        )
        scripted = ScriptedLLM(["hi"])
        creature = build_creature(str(tmp_path), llm=scripted, io="none")
        assert creature.agent.llm is scripted
        assert creature.config_name == "scripted"
        assert creature.config_ref == str(tmp_path)
        assert creature.agent.plugins.is_enabled("goal")
        assert (
            sum(
                plugin["name"] == "goal"
                for plugin in creature.agent.plugins.list_plugins()
            )
            == 1
        )


# ── warm pause + kill markers (UXI-11) ─────────────────────────


class TestCreatureLifecycleMarkers:
    def test_pause_resume_delegate_to_agent(self):
        agent = _FakeAgent(name="w")
        agent._paused = False
        agent.pause = lambda: setattr(agent, "_paused", True)
        agent.resume = lambda: setattr(agent, "_paused", False)
        c = _creature(name="w", agent=agent)
        assert c.paused is False
        c.pause()
        assert c.paused is True
        c.resume()
        assert c.paused is False

    async def test_killed_marker_reset_on_start(self):
        agent = _FakeAgent(name="w")
        c = _creature(name="w", agent=agent)
        assert c.killed is False
        c._killed = True
        assert c.killed is True
        # A fresh start clears the killed marker.
        await c.start()
        try:
            assert c.killed is False
        finally:
            await c.stop()

    def test_get_status_reports_paused_and_killed(self):
        agent = _FakeAgent(name="w")
        agent._paused = True
        c = _creature(name="w", agent=agent)
        c._killed = True
        status = c.get_status()
        assert status["paused"] is True
        assert status["killed"] is True


# ── typed turn drivers on Creature (E3) ────────────────────────


class TestCreatureTurnAPI:
    def _creature_cfg(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "name: turnc\ninput:\n  type: none\noutput:\n  type: none\n",
            encoding="utf-8",
        )
        return str(tmp_path)

    async def test_run_returns_result(self, tmp_path):
        c = build_creature(
            self._creature_cfg(tmp_path),
            llm=ScriptedLLM(["creature reply"]),
            io="headless",
        )
        await c.start()
        try:
            result = await c.run("hi")
            assert result.ok
            assert "creature reply" in result.text
        finally:
            await c.stop()

    async def test_attach_streams_a_chat_turn(self, tmp_path):
        from kohakuterrarium.core.turn import TextChunk

        c = build_creature(
            self._creature_cfg(tmp_path),
            llm=ScriptedLLM(["observed"]),
            io="headless",
        )
        await c.start()
        try:
            async with c.attach() as stream:
                await c.run("hi")
                text = ""
                while "observed" not in text:
                    ev = await asyncio.wait_for(stream._queue.get(), timeout=2)
                    if isinstance(ev, TextChunk):
                        text += ev.text
                assert "observed" in text
        finally:
            await c.stop()


# ── build_creature: io= modes (E12) ────────────────────────────


class TestBuildCreatureIOModes:
    def _write_cfg(self, tmp_path):
        # Config declares cli input + stdout output — the io= modes
        # must override what the config says.
        (tmp_path / "config.yaml").write_text(
            "name: iomodes\ninput:\n  type: none\noutput:\n  type: stdout\n",
            encoding="utf-8",
        )
        return str(tmp_path)

    def test_headless_silences_default_output(self, tmp_path):
        creature = build_creature(
            self._write_cfg(tmp_path), llm=ScriptedLLM(["x"]), io="headless"
        )
        assert isinstance(creature.agent.output_router.default_output, NoneOutput)
        assert isinstance(creature.agent.input, NoneInput)

    def test_none_keeps_config_output(self, tmp_path):
        creature = build_creature(
            self._write_cfg(tmp_path), llm=ScriptedLLM(["x"]), io="none"
        )
        # Input suppressed, but the config's stdout output still boots.
        assert isinstance(creature.agent.input, NoneInput)
        assert isinstance(creature.agent.output_router.default_output, StdoutOutput)

    def test_invalid_io_value_raises(self, tmp_path):
        with pytest.raises(ValueError, match="io= must be"):
            build_creature(self._write_cfg(tmp_path), io="quiet")


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
        assert c.stop_requested

    async def test_only_explicit_start_clears_stop_intent(self):
        c = _creature()
        await c.start()
        await c.stop()
        assert c.stop_requested

        await c.start(requested=False)
        assert c.stop_requested
        await c.stop(requested=False)
        assert c.stop_requested

        await c.start()
        assert not c.stop_requested
        await c.stop()

    async def test_natural_idle_requires_no_turn_or_background_work(self):
        c = _creature()
        await c.start()
        c._running = False
        c.agent._running = False
        assert c.is_naturally_idle()

        c.agent._active_handles = {"direct": object()}
        assert not c.is_naturally_idle()
        c.agent._active_handles.clear()

        c.agent._event_inbox = asyncio.Queue()
        c.agent._event_inbox.put_nowait(object())
        assert not c.is_naturally_idle()
        c.agent._event_inbox.get_nowait()

        c.agent._processing_task = asyncio.create_task(asyncio.sleep(1))
        assert not c.is_naturally_idle()
        c.agent._processing_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await c.agent._processing_task

    async def test_is_running_property(self):
        c = _creature()
        assert c.is_running is False  # not_started
        await c.start()
        # is_running is a shortcut over status: idle/busy → True.
        assert c.is_running is True

    async def test_input_loop_exit_keeps_creature_message_eligible(self):
        # The creature-host _running flag flips when its _drive_input task
        # idles out, but the agent stays alive. is_running derives from status
        # (agent liveness), so the creature stays "idle" and message-eligible
        # rather than falsely reporting "not running" to group_send.
        c = _creature()
        await c.start()
        assert c.is_running is True
        c._running = False  # _drive_input idled out; agent still alive
        assert c.status == "idle"
        assert c.is_running is True

    async def test_drive_input_skipped_when_absent(self):
        # _FakeAgent has no _drive_input; start should not crash.
        c = _creature()
        await c.start()
        assert c._input_task is None

    async def test_failed_agent_start_rolls_back_partial_resources(self, tmp_path):
        class FailingTrigger(BaseTrigger):
            def __init__(self):
                super().__init__()
                self.stop_calls = 0

            async def _on_start(self):
                raise RuntimeError("startup failed")

            async def _on_stop(self):
                self.stop_calls += 1

            async def wait_for_trigger(self):
                await asyncio.Event().wait()

        (tmp_path / "config.yaml").write_text(
            "name: failing\ninput:\n  type: none\noutput:\n  type: none\n",
            encoding="utf-8",
        )
        creature = build_creature(
            str(tmp_path), llm=ScriptedLLM(["unused"]), io="headless"
        )
        trigger = FailingTrigger()
        await creature.agent.trigger_manager.add(
            trigger, trigger_id="failing", autostart=False
        )

        with pytest.raises(RuntimeError, match="startup failed"):
            await creature.start()

        assert trigger.is_running is False
        assert trigger.stop_calls == 1
        assert creature.agent.is_running is False
        assert creature.is_running is False
        assert creature.restoration_state == "added"

        await creature.stop()
        assert trigger.stop_calls == 1


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


# ── restoration barrier (design §6.5, Phase E) ────────────────


class _BarrierAgent(_FakeAgent):
    """Fake agent that exposes the ``_startup_settled`` observable."""

    def __init__(self, name="barrier"):
        super().__init__(name=name)
        self._startup_settled = asyncio.Event()


class TestRestorationBarrier:
    async def test_starts_at_added(self):
        c = _creature()
        assert c.restoration_state == "added"
        assert c.restoration_ready is False

    async def test_fresh_creature_reaches_ready_when_no_startup(self):
        # A fake agent with no startup-settle observable is treated as
        # settled at once — a session-less fresh creature is promptly ready.
        c = _creature()
        await c.start()
        await c.wait_restoration_ready()
        assert c.restoration_state == "restoration_ready"
        assert c.restoration_ready is True
        await c.stop()

    async def test_barrier_waits_for_startup_settlement(self):
        agent = _BarrierAgent()
        c = _creature(agent=agent)
        await c.start()
        await asyncio.sleep(0)  # let the barrier task reach the wait
        # Startup has not settled — the barrier is not crossed yet.
        assert c.restoration_state == "started"
        assert c.restoration_ready is False
        agent._startup_settled.set()
        await c.wait_restoration_ready()
        assert c.restoration_ready is True
        await c.stop()

    async def test_stop_resets_barrier(self):
        c = _creature()
        await c.start()
        await c.wait_restoration_ready()
        await c.stop()
        # Stop tears the barrier down — a restart must re-arm it.
        assert c.restoration_ready is False
        assert c.restoration_state == "added"

    async def test_restart_rearms_barrier(self):
        agent = _BarrierAgent()
        c = _creature(agent=agent)
        await c.start()
        agent._startup_settled.set()
        await c.wait_restoration_ready()
        await c.stop()
        # Fresh cycle: the observable is re-cleared by agent.start(), so a
        # new barrier gates the second run.
        agent._startup_settled.clear()
        await c.start()
        await asyncio.sleep(0)
        assert c.restoration_ready is False
        agent._startup_settled.set()
        await c.wait_restoration_ready()
        assert c.restoration_ready is True
        await c.stop()


# ── inject_event (public creature ingress) ────────────────────


class TestInjectEvent:
    def _cfg(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "name: injc\ninput:\n  type: none\noutput:\n  type: none\n",
            encoding="utf-8",
        )
        return str(tmp_path)

    async def test_runs_event_and_echoes_correlation(self, tmp_path):
        from kohakuterrarium.core.events import TriggerEvent

        c = build_creature(
            self._cfg(tmp_path), llm=ScriptedLLM(["drive done"]), io="headless"
        )
        await c.start()
        try:
            event = TriggerEvent(
                type="drive_ready", content="pursue", context={}, stackable=False
            )
            result = await c.inject_event(event, correlation_id="d-7")
            assert result.status == "ok"
            assert "drive done" in result.text
            assert result.correlation_id == "d-7"
        finally:
            await c.stop()

    async def test_rejects_when_stopped_not_silently(self, tmp_path):
        from kohakuterrarium.core.events import TriggerEvent

        c = build_creature(
            self._cfg(tmp_path), llm=ScriptedLLM(["never"]), io="headless"
        )
        # Never started — the event must not run.
        event = TriggerEvent(type="drive_ready", content="x", stackable=False)
        result = await c.inject_event(event, correlation_id="d-1")
        assert result.status == "rejected"
        assert result.correlation_id == "d-1"
        assert result.text == ""


class TestApplyCreatureName:
    """P0 regression pins — the display-name rename must follow onto
    every name-keyed recorder, INCLUDING a SessionOutput attached
    before the rename (the engine's autosession attaches during
    add_creature; events recorded under the stale config name are
    invisible to history reads, which use the display name)."""

    def _creature(self, agent_name="alice"):
        from types import SimpleNamespace

        session_output = SimpleNamespace(
            _agent_name=agent_name, _event_key_prefix=agent_name
        )
        agent = SimpleNamespace(
            config=SimpleNamespace(name=agent_name),
            executor=SimpleNamespace(_agent_name=agent_name),
            trigger_manager=SimpleNamespace(_agent_name=agent_name),
            compact_manager=SimpleNamespace(_agent_name=agent_name),
            _session_output=session_output,
        )
        creature = SimpleNamespace(
            name=agent_name,
            agent=agent,
            config=SimpleNamespace(name=agent_name),
            config_name=agent_name,
        )
        return creature, session_output

    def test_rename_retargets_session_output(self):
        from kohakuterrarium.terrarium.creature_host import apply_creature_name

        creature, out = self._creature()
        apply_creature_name(creature, "warm-ember")
        assert creature.name == "warm-ember"
        assert creature.agent.config.name == "warm-ember"
        assert creature.config_name == "alice"
        # The live event recorder follows — future events key under the
        # display name the history endpoint resolves.
        assert out._agent_name == "warm-ember"
        assert out._event_key_prefix == "warm-ember"

    def test_runtime_rename_preserves_config_identity(self, tmp_path):
        from kohakuterrarium.terrarium.creature_host import apply_creature_name

        (tmp_path / "config.yaml").write_text(
            "name: swe\ninput:\n  type: none\noutput:\n  type: none\n",
            encoding="utf-8",
        )
        creature = build_creature(str(tmp_path), llm=ScriptedLLM(["ok"]), io="headless")

        apply_creature_name(creature, "warm-ember")

        assert creature.name == "warm-ember"
        assert creature.config_name == "swe"
        assert creature.config_ref == str(tmp_path)
        status = creature.get_status()
        assert status["name"] == "warm-ember"
        assert status["config_name"] == "swe"
        assert status["config_ref"] == str(tmp_path)

    def test_rename_keeps_custom_attached_prefix(self):
        from kohakuterrarium.terrarium.creature_host import apply_creature_name

        creature, out = self._creature()
        # Wave F attached agents record under a host-scoped namespace —
        # a rename must not clobber it.
        out._event_key_prefix = "host:attached:reviewer:1"
        apply_creature_name(creature, "warm-ember")
        assert out._agent_name == "warm-ember"
        assert out._event_key_prefix == "host:attached:reviewer:1"

    def test_rename_without_session_output(self):
        from kohakuterrarium.terrarium.creature_host import apply_creature_name

        creature, _ = self._creature()
        creature.agent._session_output = None
        apply_creature_name(creature, "warm-ember")
        assert creature.agent.config.name == "warm-ember"
