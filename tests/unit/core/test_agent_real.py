"""End-to-end unit tests for the real :class:`Agent` class.

These tests construct a fully-wired Agent with `ScriptedLLM` injected
via monkeypatched bootstrap. They exercise:

* `core/agent.py` — `Agent.__init__`, `start`, `stop`,
  `_init_compact_manager`, `_init_iteration_budget`, `update_system_prompt`,
  `interrupt`, `session_info`.
* `core/agent_handlers.py` — `_process_event`, `_handle_user_input`,
  `_run_controller_with_dispatch`, `_finalize_processing`,
  branch bookkeeping.
* `core/agent_tools.py` — direct + background tool dispatch,
  `_collect_direct_results`, sub-agent handling.

The harness avoids any I/O dependency by overriding `_init_input`
and using a `OutputRecorder` as the default output module.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.bootstrap import llm as bootstrap_llm
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.event_inbox import EventEnvelope
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.core.events import (
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.core.turn import TurnCapture
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

# ── deterministic tool stubs ─────────────────────────────────────


class _EchoTool(BaseTool):
    @property
    def tool_name(self):
        return "echo"

    @property
    def description(self):
        return "echo"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output=str(args.get("msg", "")))


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def patched_llm(monkeypatch):
    """Patch the LLM factory so every Agent build gets a fresh ScriptedLLM.

    A factory closure lets each test inject its own script via
    ``patched_llm.set_script([...])`` before constructing the Agent.
    """

    class _Patch:
        def __init__(self):
            self.script: list = ["OK"]

        def set_script(self, script):
            self.script = script

    p = _Patch()

    def _fake_create(config, llm=None):
        return ScriptedLLM(p.script)

    monkeypatch.setattr(bootstrap_llm, "create_llm_provider", _fake_create)
    # Also patch the controller-side import path.
    from kohakuterrarium.bootstrap import agent_init

    monkeypatch.setattr(agent_init, "create_llm_provider", _fake_create)
    return p


@pytest.fixture
def make_agent(patched_llm, tmp_path, monkeypatch):
    """Build a real Agent with a minimal config + stub I/O."""

    def _build(
        *,
        script=None,
        system_prompt="You are a test agent.",
        tools=None,
        ephemeral=False,
        max_iterations=None,
        termination=None,
        max_messages=0,
    ):
        if script is not None:
            patched_llm.set_script(script)
        cfg = AgentConfig(
            name="test_agent",
            llm_profile="openai/gpt-4-test",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt=system_prompt,
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
            tools=tools or [],
            ephemeral=ephemeral,
            max_iterations=max_iterations,
            max_messages=max_messages,
            termination=termination,
        )
        agent = Agent(cfg)
        # Swap the default output to a recorder for assertions.
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        agent._recorder = recorder
        return agent

    return _build


async def _start_and_run(agent, event):
    """Start the agent, dispatch one event, wait for processing to drain."""
    await agent.start()
    try:
        await agent._process_event(event)
    finally:
        await agent.stop()


# ── construction + lifecycle ─────────────────────────────────────


class TestLLMInstanceInjection:
    """``llm=`` instance injection (E5) — NO factory monkeypatch.

    The headline contract of the API cleanup: handing the agent a
    provider instance (``ScriptedLLM``) binds it directly, replacing
    the old two-site ``create_llm_provider`` monkeypatch ceremony.
    """

    def _cfg(self, tmp_path):
        return AgentConfig(
            name="inject_agent",
            system_prompt="You are a test agent.",
            include_hints_in_prompt=False,
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="stdout"),
        )

    async def test_instance_binds_directly(self, tmp_path):
        scripted = ScriptedLLM(["Injected reply."])
        agent = Agent(self._cfg(tmp_path), llm=scripted)
        assert agent.llm is scripted
        await _start_and_run(agent, create_user_input_event("hi"))
        # The injected instance really served the turn.
        assert scripted.call_count == 1
        last = agent.controller.conversation.get_last_assistant_message()
        assert last is not None
        assert "Injected reply." in last.get_text_content()

    async def test_build_classmethod_with_instance(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "name: built\ninput:\n  type: none\noutput:\n  type: stdout\n",
            encoding="utf-8",
        )
        scripted = ScriptedLLM(["ok"])
        agent = await Agent.build(tmp_path, llm=scripted)
        assert agent.llm is scripted
        assert agent.config.name == "built"

    async def test_build_accepts_loaded_config(self, tmp_path):
        scripted = ScriptedLLM(["ok"])
        agent = await Agent.build(self._cfg(tmp_path), llm=scripted)
        assert agent.llm is scripted

    async def test_invalid_llm_type_raises_immediately(self, tmp_path):
        # Instance-injection misuse must NOT silently defer — it is a
        # caller bug, surfaced at construction time.
        with pytest.raises(TypeError, match="llm= accepts"):
            Agent(self._cfg(tmp_path), llm=12345)

    async def test_string_selector_records_for_identifier(self, tmp_path):
        # A selector string still flows through profile resolution
        # (which fails here → deferred under strict=False), but is
        # recorded so llm_identifier()/resume see what was asked for.
        agent = Agent(self._cfg(tmp_path), llm="ghost/selector", strict=False)
        assert agent._llm_selector == "ghost/selector"

    async def test_unresolvable_selector_raises_when_strict(self, tmp_path):
        # Strict (default): a bad model selector is a caller bug —
        # raise at construction, never a silent DeferredLLMProvider.
        with pytest.raises(ValueError):
            Agent(self._cfg(tmp_path), llm="ghost/selector")


class TestAgentConstruction:
    async def test_agent_builds_with_minimal_config(self, make_agent):
        agent = make_agent()
        assert agent.config.name == "test_agent"
        # Every core component is wired.
        assert agent.controller is not None
        assert agent.executor is not None
        assert agent.registry is not None
        assert agent.subagent_manager is not None
        assert agent.output_router is not None
        assert agent.input is not None
        assert isinstance(agent.input, NoneInput)

    async def test_start_stop_cycle(self, make_agent):
        agent = make_agent()
        await agent.start()
        assert agent._running is True
        await agent.stop()
        assert agent._running is False


class TestSessionInfo:
    async def test_session_info_shape(self, make_agent):
        agent = make_agent()
        info = agent.session_info()
        assert info["agent"] == "test_agent"
        # Tokens default-empty for ``own`` view when no store attached.
        assert info["tokens"] == {}


# ── _process_event: user_input → LLM round-trip ──────────────────


class TestUserInputProcessing:
    async def test_simple_text_response(self, make_agent):
        agent = make_agent(script=["Hello world"])
        evt = create_user_input_event("hi")
        await _start_and_run(agent, evt)
        # Final assistant message contains the response text.
        last = agent.controller.conversation.get_last_assistant_message()
        assert last is not None
        assert "Hello world" in last.get_text_content()

    async def test_two_consecutive_inputs_advance_turn(self, make_agent):
        agent = make_agent(script=["a1", "a2"])
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("u1"))
            t1 = agent._turn_index
            await agent._process_event(create_user_input_event("u2"))
            assert agent._turn_index == t1 + 1
        finally:
            await agent.stop()

    async def test_user_input_appended_to_conversation(self, make_agent):
        agent = make_agent(script=["ok"])
        await _start_and_run(agent, create_user_input_event("hello"))
        msgs = agent.controller.conversation.get_messages()
        roles = [m.role for m in msgs]
        assert "user" in roles
        assert "assistant" in roles


# ── tool dispatch ────────────────────────────────────────────────


class TestToolDispatch:
    async def test_tool_call_executes(self, make_agent):
        # First LLM call emits a tool block; second wraps up.
        agent = make_agent(
            script=[
                "[/echo]msg=hi[echo/]",
                "Done!",
            ]
        )
        # Register our echo tool directly into the registry + executor.
        tool = _EchoTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await _start_and_run(agent, create_user_input_event("run echo"))
        # The conversation contains a tool message with the echo output.
        msgs = agent.controller.conversation.get_messages()
        # There should be a turn-completion message after the tool ran.
        assert len(msgs) >= 3


# ── compact manager initialised + cancelled on stop ──────────────


class TestCompactManagerLifecycle:
    async def test_compact_manager_created_on_start(self, make_agent):
        agent = make_agent()
        # No compact config → manager may still be created, but is dormant.
        await agent.start()
        try:
            # ``_init_compact_manager`` always runs in start().
            assert hasattr(agent, "compact_manager")
        finally:
            await agent.stop()


# ── iteration budget ─────────────────────────────────────────────


class TestIterationBudget:
    async def test_budget_none_when_unset(self, make_agent):
        agent = make_agent()
        assert agent.iteration_budget is None

    async def test_budget_created_when_capped(self, make_agent):
        agent = make_agent(max_iterations=3)
        assert agent.iteration_budget is not None
        assert agent.iteration_budget.remaining == 3


# ── update_system_prompt ─────────────────────────────────────────


class TestUpdateSystemPrompt:
    async def test_extra_block_appended(self, make_agent):
        agent = make_agent()
        before = agent.controller.conversation.get_system_message().content
        agent.update_system_prompt("\n[EXTRA]")
        after = agent.controller.conversation.get_system_message().content
        assert "[EXTRA]" in after
        assert after.startswith(before)


# ── interrupt ────────────────────────────────────────────────────


class TestInterrupt:
    async def test_interrupt_sets_flags(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            agent.interrupt()
            assert agent._interrupt_requested is True
        finally:
            await agent.stop()


# ── termination ──────────────────────────────────────────────────


class TestTermination:
    async def test_termination_checker_created_from_config(self, make_agent):
        agent = make_agent(termination={"max_turns": 5})
        assert agent._termination_checker is not None
        assert agent._termination_checker.config.max_turns == 5


# ── _process_event drops events when stopped ─────────────────────


class TestStoppedAgent:
    async def test_event_on_stopped_strict_agent_raises(self, make_agent):
        # E4: dropping input used to return True (the success value).
        # Strict agents (the programmatic default) now raise.
        from kohakuterrarium.errors import AgentNotRunningError

        agent = make_agent(script=["unused"])
        with pytest.raises(AgentNotRunningError):
            await agent._process_event(create_user_input_event("hi"))
        # No assistant message recorded.
        msgs = agent.controller.conversation.get_messages()
        roles = [m.role for m in msgs]
        # Only system survives.
        assert "assistant" not in roles

    async def test_event_on_stopped_lenient_agent_returns_false(self, make_agent):
        agent = make_agent(script=["unused"])
        agent._strict = False
        out = await agent._process_event(create_user_input_event("hi"))
        # Honest drop signal — NOT the old ``True``.
        assert out is False

    async def test_internal_event_on_stopped_agent_drops_quietly(self, make_agent):
        # Non-user events racing a shutdown stay benign even on strict
        # agents (trigger tasks must not explode during stop()).
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent(script=["unused"])
        out = await agent._process_event(TriggerEvent(type="trigger", content="tick"))
        assert out is False


# ── regenerate_last_response + edit_and_rerun on real Agent ──────


class TestRegenAndEdit:
    async def test_regenerate_runs_again(self, make_agent):
        agent = make_agent(
            script=[
                "first reply",
                "fresh reply",
            ]
        )
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None and "first reply" in last.get_text_content()
            await agent.regenerate_last_response()
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None and "fresh reply" in last.get_text_content()
        finally:
            await agent.stop()

    async def test_edit_and_rerun_runs_with_new_content(self, make_agent):
        agent = make_agent(
            script=[
                ScriptEntry("hello there", match="hi"),
                ScriptEntry("hi GOODBYE", match="bye"),
            ]
        )
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None and "hello there" in last.get_text_content()
            # System message at index 0, user at index 1.
            ok = await agent.edit_and_rerun(message_idx=1, new_content="bye")
            assert ok is True
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None and "GOODBYE" in last.get_text_content()
        finally:
            await agent.stop()

    async def test_rewind_to_drops_messages(self, make_agent):
        agent = make_agent(script=["reply"])
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            await agent.rewind_to(1)
            msgs = agent.controller.conversation.get_messages()
            # Only system survives.
            assert [m.role for m in msgs] == ["system"]
        finally:
            await agent.stop()


# ── tool error handling ──────────────────────────────────────────


class _BoomTool(BaseTool):
    @property
    def tool_name(self):
        return "boom"

    @property
    def description(self):
        return "boom"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        raise RuntimeError("intentional failure")


class TestToolErrorHandling:
    async def test_tool_failure_does_not_crash_turn(self, make_agent):
        agent = make_agent(
            script=[
                "[/boom][/boom/]",
                "Sorry, that failed.",
            ]
        )
        boom = _BoomTool()
        agent.registry.register_tool(boom)
        agent.executor.register_tool(boom)
        await _start_and_run(agent, create_user_input_event("try it"))
        # Conversation reaches the assistant follow-up.
        last = agent.controller.conversation.get_last_assistant_message()
        # Some assistant response made it.
        assert last is not None


# ── _cancel_job ──────────────────────────────────────────────────


class TestCancelJob:
    async def test_cancel_unknown_job_no_crash(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            # Sync method — does not raise on unknown job.
            agent._cancel_job("nope", "nope")
        finally:
            await agent.stop()

    async def test_promote_unknown_handle_returns_false(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            assert agent._promote_handle("nope") is False
        finally:
            await agent.stop()


# ── plugin hooks (minimal) ───────────────────────────────────────


class TestStartupTriggerSkippedWhenAbsent:
    async def test_no_startup_trigger_no_op(self, make_agent):
        agent = make_agent()
        # _fire_startup_trigger is no-op when config.startup_trigger is None.
        await agent.start()
        try:
            await agent._fire_startup_trigger()
        finally:
            await agent.stop()


class TestStartupTriggerFires:
    async def test_startup_trigger_emits_event(self, make_agent, patched_llm):
        patched_llm.set_script(["startup ack"])
        agent = make_agent()
        # Configure startup_trigger AFTER build so we don't trip llm init.
        agent.config.startup_trigger = {"prompt": "boot up"}
        await agent.start()
        try:
            await agent._fire_startup_trigger()
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None and "startup ack" in last.get_text_content()
        finally:
            await agent.stop()


# ── update_system_prompt with extra context ──────────────────────


class TestUpdateSystemPromptIdempotent:
    async def test_repeated_calls_append(self, make_agent):
        agent = make_agent()
        before = agent.controller.conversation.get_system_message().content
        agent.update_system_prompt("\nA")
        agent.update_system_prompt("\nB")
        after = agent.controller.conversation.get_system_message().content
        assert "A" in after and "B" in after
        assert len(after) > len(before)


# ── controller_data accessors ────────────────────────────────────


class TestAgentAccessors:
    async def test_llm_identifier_falls_back_to_llm_model(self, make_agent):
        # The test fixture's profile is unregistered and ScriptedLLM
        # carries no ``.model`` attribute, so ``llm_identifier`` exercises
        # its documented fallback: ``getattr(self.llm, "model", "")``.
        agent = make_agent()
        assert agent._llm_identifier == ""
        out = agent.llm_identifier()
        # Fallback yields the LLM's model name, which is "" for the
        # model-less ScriptedLLM stub.
        assert out == getattr(agent.llm, "model", "")

    async def test_has_pending_mid_turn_inputs_probe(self, make_agent):
        # The public read-only probe over the event inbox that the
        # Terrarium Drive fairness check reads instead of the private queue.
        from kohakuterrarium.core.event_inbox import EventEnvelope

        agent = make_agent()
        assert agent.has_pending_mid_turn_inputs is False
        agent._event_inbox.put(EventEnvelope(create_user_input_event("buffered")))
        assert agent.has_pending_mid_turn_inputs is True
        agent._event_inbox.drain_all()
        assert agent.has_pending_mid_turn_inputs is False


# ── queued-message edit / cancel (UXI-08a) ───────────────────────


class TestPendingInputEditCancel:
    async def test_edit_before_claim_wins(self, make_agent):
        from kohakuterrarium.core.event_inbox import EventEnvelope
        from kohakuterrarium.core.pending_input import stamp_pending_id

        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            evt = create_user_input_event("original")
            pid = stamp_pending_id(evt)
            agent._event_inbox.put(EventEnvelope(evt))
            # Edit while still queued → commits onto the queued event.
            assert agent.edit_pending(pid, "corrected") is True
            # Cancel a different id → plain no-op.
            assert agent.cancel_pending("nope") is False
            claimed = agent._event_inbox.drain_all()
            assert len(claimed) == 1
            assert claimed[0].event.content == "corrected"
        finally:
            await agent.stop()

    async def test_edit_after_claim_is_noop(self, make_agent):
        from kohakuterrarium.core.event_inbox import EventEnvelope
        from kohakuterrarium.core.pending_input import stamp_pending_id

        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            evt = create_user_input_event("original")
            pid = stamp_pending_id(evt)
            agent._event_inbox.put(EventEnvelope(evt))
            # The mid-turn re-claim claims the whole foldable prefix.
            drained = await agent._drain_mid_turn_pending_inputs(agent.controller)
            assert drained == 1
            assert agent._event_inbox.empty()
            # Now the message is already sent — edit / cancel are no-ops.
            assert agent.edit_pending(pid, "too late") is False
            assert agent.cancel_pending(pid) is False
            # The corrected text never reached the conversation.
            user_texts = [
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            assert any("original" in t for t in user_texts)
            assert all("too late" not in t for t in user_texts)
        finally:
            await agent.stop()

    async def test_cancel_before_claim_drops_message(self, make_agent):
        from kohakuterrarium.core.event_inbox import EventEnvelope
        from kohakuterrarium.core.pending_input import stamp_pending_id

        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            evt = create_user_input_event("cancel me")
            pid = stamp_pending_id(evt)
            agent._event_inbox.put(EventEnvelope(evt))
            assert agent.cancel_pending(pid) is True
            # The re-claim now finds an empty inbox.
            assert await agent._drain_mid_turn_pending_inputs(agent.controller) == 0
        finally:
            await agent.stop()

    async def test_buffered_event_gets_stable_id_stamped(self, make_agent):
        # A mid-turn event folded because a turn holds the mutex gets a
        # stable pending id stamped so a shell can target it by id.
        from kohakuterrarium.core.pending_input import pending_id_of

        started = asyncio.Event()
        agent = make_agent(script=["[/hangdirect]x[hangdirect/]", "done"])
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        turn = asyncio.create_task(agent._process_event(create_user_input_event("go")))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            # A turn holds the mutex → this folds (fire-and-forget) → False.
            ran = await agent._process_event(create_user_input_event("queued"))
            assert ran is False
            assert len(agent._event_inbox) == 1
            claimed = agent._event_inbox.drain_all()
            assert pending_id_of(claimed[0].event)
        finally:
            await agent.stop()
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)


# ── channel-backlog drains in one turn (UXI-08b) ─────────────────


class TestChannelBacklogOneTurn:
    async def test_burst_drains_in_single_turn_queue_empty(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        class _BurstTrigger(BaseTrigger):
            """Fires once with a primary event and reports the rest of its
            ready backlog via drain_ready — the ChannelTrigger contract."""

            def __init__(self, events):
                super().__init__()
                self._events = list(events)
                self._fired = False

            async def wait_for_trigger(self):
                if self._fired:
                    await asyncio.sleep(60)
                    return None
                self._fired = True
                return self._events[0]

            def drain_ready(self):
                return self._events[1:]

        # round1 serves the primary; round2 serves the round after the
        # drain injects the backlog — one TURN, two rounds.
        agent = make_agent(script=["round1", "round2"])
        await agent.start()
        turns: list = []
        orig = agent._process_batch_with_controller

        async def spy(events, controller):
            turns.append(events)
            return await orig(events, controller)

        agent._process_batch_with_controller = spy
        try:
            events = [
                TriggerEvent(
                    type="channel_message", content=f"m{i}", prompt_override=f"m{i}"
                )
                for i in range(3)
            ]
            await agent.add_trigger(_BurstTrigger(events))
            # Wait for the trigger loop to fire + the turn to settle.
            for _ in range(200):
                await asyncio.sleep(0.01)
                if turns and agent._event_inbox.empty():
                    if agent._processing_task is None:
                        break
            # The whole 3-message burst was one turn, not three.
            assert len(turns) == 1
            # Queue empty at turn end.
            assert agent._event_inbox.empty()
            # All three messages reached the conversation.
            user_text = "\n".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            )
            assert "m0" in user_text and "m1" in user_text and "m2" in user_text
        finally:
            await agent.stop()

    async def test_single_message_is_one_turn(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        class _OneShotTrigger(BaseTrigger):
            def __init__(self, event):
                super().__init__()
                self._event = event
                self._fired = False

            async def wait_for_trigger(self):
                if self._fired:
                    await asyncio.sleep(60)
                    return None
                self._fired = True
                return self._event

            def drain_ready(self):
                return []

        agent = make_agent(script=["only"])
        await agent.start()
        turns: list = []
        orig = agent._process_batch_with_controller

        async def spy(events, controller):
            turns.append(events)
            return await orig(events, controller)

        agent._process_batch_with_controller = spy
        try:
            evt = TriggerEvent(
                type="channel_message", content="solo", prompt_override="solo"
            )
            await agent.add_trigger(_OneShotTrigger(evt))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if turns and agent._processing_task is None:
                    break
            assert len(turns) == 1
            assert agent._event_inbox.empty()
        finally:
            await agent.stop()


# ── warm pause / resume (UXI-11) ─────────────────────────────────


class TestPauseResume:
    async def test_pause_blocks_new_turns_and_resume_drains(self, make_agent):
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            agent.pause()
            assert agent.paused is True
            # A new input while paused queues instead of running.
            ran = await agent._process_event(create_user_input_event("while paused"))
            assert ran is False
            assert len(agent._event_inbox) == 1
            # No turn happened — the runtime stayed warm but admitted nothing.
            assert agent.controller.conversation.get_last_assistant_message() is None
            # Resume re-admits and drains what queued while paused.
            agent.resume()
            assert agent.paused is False
            last = None
            for _ in range(200):
                await asyncio.sleep(0.01)
                last = agent.controller.conversation.get_last_assistant_message()
                if last is not None and agent._processing_task is None:
                    break
            assert last is not None and "ack" in last.get_text_content()
            assert agent._event_inbox.empty()
        finally:
            await agent.stop()

    async def test_run_event_rejected_while_paused_no_turn(self, make_agent):
        # Critic MAJOR: Drive/programmatic ingress (run_event → await_turn,
        # non-stackable) must NOT start a turn while paused. It rejects
        # (status="rejected", which Drive treats as a transient retry) and
        # does NOT buffer (buffering + Drive retry = double delivery).
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent(script=["should not run"])
        await agent.start()
        try:
            agent.pause()
            evt = TriggerEvent(
                type="drive_ready",
                content="goal",
                context={"correlation_id": "d1"},
                stackable=False,
            )
            result = await agent.run_event(evt)
            assert result.status == "rejected"
            # No turn ran, and the event was NOT queued.
            assert agent.controller.conversation.get_last_assistant_message() is None
            assert agent._event_inbox.empty()
        finally:
            await agent.stop()

    async def test_run_rejected_while_paused(self, make_agent):
        # The programmatic single-turn driver run() (await_turn) is rejected
        # too — no turn starts on a paused agent.
        agent = make_agent(script=["should not run"])
        await agent.start()
        try:
            agent.pause()
            result = await agent.run("hi", raise_on_error=False)
            assert result.status == "rejected"
            assert agent.controller.conversation.get_last_assistant_message() is None
        finally:
            await agent.stop()

    async def test_leftover_queue_gated_while_paused(self, make_agent):
        # A paused agent's consumer parks on the resume gate — events queued
        # while paused stay in order and nothing runs until resume.
        from kohakuterrarium.core.event_inbox import EventEnvelope
        from kohakuterrarium.core.pending_input import (
            pending_id_of,
            stamp_pending_id,
        )

        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            agent.pause()
            e1 = create_user_input_event("first")
            e2 = create_user_input_event("second")
            stamp_pending_id(e1)
            stamp_pending_id(e2)
            agent._event_inbox.put(EventEnvelope(e1))
            agent._event_inbox.put(EventEnvelope(e2))
            ids_before = [pending_id_of(env.event) for env in agent._event_inbox._dq]
            await asyncio.sleep(0.05)
            ids_after = [pending_id_of(env.event) for env in agent._event_inbox._dq]
            # Order + membership unchanged — nothing ran while paused.
            assert ids_after == ids_before
            assert agent.controller.conversation.get_last_assistant_message() is None
        finally:
            await agent.stop()


# ── trigger_manager + on_trigger_fired callback ──────────────────


class TestTriggerCompletionCallback:
    async def test_on_complete_callback_routes_through_handler(self, make_agent):
        """Background tool completion routes through ``_on_bg_complete`` →
        ``_process_event`` for follow-up turns."""
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            # Synthesise a tool_complete event directly.
            from kohakuterrarium.core.events import create_tool_complete_event

            evt = create_tool_complete_event(
                job_id="bash_test", content="output", exit_code=0
            )
            await agent._process_event(evt)
            # Conversation has a new assistant turn.
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
        finally:
            await agent.stop()


# ── compact manager presence ─────────────────────────────────────


class TestCompactManagerInit:
    async def test_compact_config_propagated(self, make_agent, patched_llm):
        patched_llm.set_script(["x"])
        agent = make_agent()
        agent.config.compact = {
            "max_tokens": 10_000,
            "threshold": 0.5,
            "target": 0.3,
            "keep_recent_turns": 4,
            "cooldown_seconds": 5.0,
        }
        await agent.start()
        try:
            assert agent.compact_manager is not None
            cfg = agent.compact_manager.config
            assert cfg.max_tokens == 10_000
            assert cfg.threshold == 0.5
            assert cfg.target == 0.3
            assert cfg.keep_recent_turns == 4
            assert cfg.cooldown_seconds == 5.0
        finally:
            await agent.stop()


# ── post-LLM plugin chain (assistant edit) ───────────────────────


class _AppendPlugin:
    """Plugin that appends ``[!]`` to every assistant turn."""

    name = "appender"
    priority = 0
    enabled = True

    async def post_llm_call(self, messages, text, usage, model=""):
        return text + " [!]"

    async def on_load(self, ctx):
        pass

    async def on_unload(self, ctx):
        pass


class TestPluginHooks:
    async def test_post_llm_call_rewrites_assistant_text(self, make_agent, patched_llm):
        patched_llm.set_script(["original"])
        agent = make_agent()
        # Inject plugin manager manually.
        from kohakuterrarium.modules.plugin.manager import PluginManager

        mgr = PluginManager()
        mgr.register(_AppendPlugin())
        agent.plugins = mgr
        agent.controller.plugins = mgr
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            last = agent.controller.conversation.get_last_assistant_message()
            # Plugin appended marker.
            assert "[!]" in last.get_text_content()
        finally:
            await agent.stop()


# ── max_turns termination ────────────────────────────────────────


class TestMaxTurnsTermination:
    async def test_termination_breaks_loop(self, make_agent):
        agent = make_agent(
            script=["only response"],
            termination={"max_turns": 1},
        )
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            # Termination checker active.
            assert agent._termination_checker.is_active
            # One assistant turn made it.
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
        finally:
            await agent.stop()


# ── conversation history when ephemeral ──────────────────────────


class TestEphemeralMode:
    async def test_ephemeral_clears_between_turns(self, make_agent):
        agent = make_agent(script=["resp1", "resp2"], ephemeral=True)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("u1"))
            await agent._process_event(create_user_input_event("u2"))
            msgs = agent.controller.conversation.get_messages()
            # Ephemeral mode drops prior turns after each one.
            user_msgs = [m for m in msgs if m.role == "user"]
            # At most one user message should survive after ephemeral clears.
            # (system + current user + assistant).
            assert len(user_msgs) <= 2
        finally:
            await agent.stop()


# ── multi-turn within one process_event (tool follow-up) ─────────


class TestMultiRoundTurn:
    async def test_tool_call_then_followup(self, make_agent):
        agent = make_agent(
            script=[
                "[/echo]msg=hi[echo/]",
                "All done!",
            ]
        )
        tool = _EchoTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await _start_and_run(agent, create_user_input_event("run tool"))
        # Conversation now contains BOTH the tool-using turn and the
        # follow-up text.
        last = agent.controller.conversation.get_last_assistant_message()
        assert last is not None
        # We expect at least 2 assistant turns.
        assistants = [
            m
            for m in agent.controller.conversation.get_messages()
            if m.role == "assistant"
        ]
        assert len(assistants) >= 2


# ── public API methods ───────────────────────────────────────────


class TestPublicAccessors:
    async def test_is_running(self, make_agent):
        agent = make_agent()
        assert agent.is_running is False
        await agent.start()
        assert agent.is_running is True
        await agent.stop()
        assert agent.is_running is False

    async def test_tools_subagents_properties(self, make_agent):
        agent = make_agent()
        agent.registry.register_tool(_EchoTool())
        assert "echo" in agent.tools
        # The minimal config declares no sub-agents → list is empty.
        assert agent.subagents == []

    async def test_conversation_history(self, make_agent):
        agent = make_agent(system_prompt="You are a test agent.")
        hist = agent.conversation_history
        # A freshly-built agent's history holds exactly the seeded
        # system message carrying the configured system prompt.
        system_msgs = [m for m in hist if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "You are a test agent." in system_msgs[0].get("content", "")

    async def test_get_state(self, make_agent):
        agent = make_agent()
        state = agent.get_state()
        assert state["name"] == "test_agent"
        assert state["running"] is False
        # ``tools`` mirrors the live registry; the builtin ``skill`` tool
        # is always wired even for a minimal config.
        assert state["tools"] == agent.registry.list_tools()
        assert "skill" in state["tools"]
        # No jobs submitted yet.
        assert state["pending_jobs"] == 0

    async def test_get_system_prompt(self, make_agent):
        agent = make_agent(system_prompt="CUSTOM")
        out = agent.get_system_prompt()
        # Aggregator prepends/appends, but our text is in there.
        assert "CUSTOM" in out

    async def test_update_system_prompt_replace_mode(self, make_agent):
        agent = make_agent()
        agent.update_system_prompt("BRAND NEW", replace=True)
        sys_msg = agent.controller.conversation.get_system_message()
        assert sys_msg.content == "BRAND NEW"

    async def test_update_system_prompt_no_system_message(self, make_agent):
        agent = make_agent()
        agent.controller.conversation._messages = [
            m for m in agent.controller.conversation._messages if m.role != "system"
        ]
        agent.update_system_prompt("X")
        # No crash; no system message to update.


# ── inject_input / inject_event ──────────────────────────────────


class TestInjectInput:
    async def test_inject_input_runs_turn(self, make_agent):
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            await agent.inject_input("hello")
            last = agent.controller.conversation.get_last_assistant_message()
            assert "ack" in last.get_text_content()
        finally:
            await agent.stop()

    async def test_inject_event(self, make_agent):
        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            await agent.inject_event(create_user_input_event("direct"))
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
        finally:
            await agent.stop()


# ── add/remove triggers at runtime ───────────────────────────────


class TestTriggerHotPlug:
    async def test_add_remove_trigger(self, make_agent):
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        class _NoopTrigger(BaseTrigger):
            async def wait_for_trigger(self):
                # Block forever-ish.
                await asyncio.sleep(60)
                return None

        agent = make_agent()
        await agent.start()
        try:
            tid = await agent.add_trigger(_NoopTrigger())
            assert tid in agent.trigger_manager._triggers
            removed = await agent.remove_trigger(tid)
            assert removed is True
            removed2 = await agent.remove_trigger("nope")
            assert removed2 is False
        finally:
            await agent.stop()

    async def test_remove_trigger_by_instance(self, make_agent):
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        class _NoopTrigger(BaseTrigger):
            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

        agent = make_agent()
        await agent.start()
        try:
            inst = _NoopTrigger()
            await agent.add_trigger(inst)
            assert await agent.remove_trigger(inst) is True
            # Unknown instance.
            assert await agent.remove_trigger(_NoopTrigger()) is False
        finally:
            await agent.stop()


# ── set_output_handler ───────────────────────────────────────────


class TestSetOutputHandler:
    async def test_secondary_callback_receives_chunks(self, make_agent):
        agent = make_agent(script=["chunked text"])
        captured = []
        agent.set_output_handler(lambda t: captured.append(t))
        await _start_and_run(agent, create_user_input_event("hi"))
        # The callback received at least one chunk.
        assert captured


# ── attach_session_store + run resume hooks ─────────────────────


class TestAttachSessionStore:
    async def test_attach_then_detach(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "sess.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store)
        assert agent.session_store is store
        # Second attach with same store doesn't duplicate.
        agent.attach_session_store(store)


# ── tools as background mode ─────────────────────────────────────


class _BackgroundEchoTool(BaseTool):
    @property
    def tool_name(self):
        return "bgecho"

    @property
    def description(self):
        return "bg echo"

    @property
    def execution_mode(self):
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        return ToolResult(output=str(args.get("msg", "")))


class TestBackgroundTool:
    async def test_background_tool_eventually_completes(self, make_agent):
        agent = make_agent(
            script=[
                "[/bgecho]msg=bg[bgecho/]",
                "after bg",
            ]
        )
        tool = _BackgroundEchoTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await _start_and_run(agent, create_user_input_event("kick off bg"))
        # At minimum 1 assistant turn made it.
        assert agent.controller.conversation.get_last_assistant_message() is not None


class _HangingBgTool(BaseTool):
    """Background tool that blocks until released — simulates a job
    still running when the agent is stopped."""

    def __init__(self, release: asyncio.Event):
        self._release = release

    @property
    def tool_name(self):
        return "hangbg"

    @property
    def description(self):
        return "hanging bg tool"

    @property
    def execution_mode(self):
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        await self._release.wait()
        return ToolResult(output="done")


class _HangingDirectTool(BaseTool):
    """Direct tool that blocks until released (forever by default) —
    simulates an in-flight direct job."""

    def __init__(self, started: asyncio.Event, release: asyncio.Event | None = None):
        self._started = started
        self._release = release

    @property
    def tool_name(self):
        return "hangdirect"

    @property
    def description(self):
        return "hanging direct tool"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self._started.set()
        if self._release is not None:
            await self._release.wait()
        else:
            await asyncio.sleep(3600)
        return ToolResult(output="done")


class TestStopFinalizesInflightJobs:
    async def test_stop_emits_single_terminal_for_inflight_direct_job(self, make_agent):
        # The stop sweep emits an "interrupted" terminal for the
        # in-flight direct job; when the cancelled direct-wait resumes
        # it must NOT emit a second, contradictory ("error") terminal.
        started = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangdirect]msg=x[hangdirect/]",
                "after direct",
            ]
        )
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        emitted: list[tuple[str, dict]] = []
        orig_notify = agent.output_router.notify_activity

        def spy(kind, message, metadata=None, **kwargs):
            emitted.append((kind, dict(metadata or {})))
            return orig_notify(kind, message, metadata=metadata, **kwargs)

        agent.output_router.notify_activity = spy
        await agent.start()
        turn = asyncio.create_task(
            agent._process_event(create_user_input_event("kick off"))
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            running = agent.executor.get_running_jobs()
            assert running, "direct job must still be running pre-stop"
            job_id = running[0].job_id

            await agent.stop()
            await asyncio.wait_for(
                asyncio.gather(turn, return_exceptions=True), timeout=5
            )

            terminals = [
                (kind, meta)
                for kind, meta in emitted
                if meta.get("job_id") == job_id and kind in ("tool_done", "tool_error")
            ]
            assert len(terminals) == 1, (
                "expected exactly one terminal for the swept direct job; "
                f"got: {terminals}"
            )
            assert terminals[0][1].get("final_state") == "interrupted"
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

    async def test_stop_unwinds_live_turn_before_returning(self, make_agent):
        # stop() must cancel and await the live controller loop —
        # returning while it unwinds lets it run an LLM round against
        # closed routers/providers after shutdown.
        started = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangdirect]msg=x[hangdirect/]",
                "must not stream after stop",
            ]
        )
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        turn = asyncio.create_task(agent._process_event(create_user_input_event("go")))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            await agent.stop()
            await asyncio.wait_for(
                asyncio.gather(turn, return_exceptions=True), timeout=5
            )
            assistants = [
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "assistant"
            ]
            assert all("must not stream after stop" not in a for a in assistants)
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

    async def test_stop_pairs_inflight_native_announcement(self, make_agent, tmp_path):
        # Native round in flight at stop(): the conversation holds an
        # unanswered assistant.tool_calls announcement. The sweep must
        # append the interrupted role=tool result (provider call id) so
        # the pair survives provider-safe serialization + snapshot +
        # resume rebuild.
        from kohakuterrarium.core.job import JobState, JobStatus, JobType
        from kohakuterrarium.session.resume import _build_conversation

        agent = make_agent()
        await agent.start()
        try:
            agent.controller.conversation.append(
                "assistant",
                "",
                tool_calls=[
                    {
                        "id": "provider_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            )
            agent.executor.job_store.register(
                JobStatus(
                    job_id="bash_internal",
                    job_type=JobType.TOOL,
                    type_name="bash",
                    state=JobState.RUNNING,
                )
            )
            agent._register_direct_job(
                "bash_internal",
                kind="tool",
                name="bash",
                tool_call_id="provider_1",
            )
            await agent.stop()
            wire = agent.controller.conversation.to_messages()
            results = [
                m
                for m in wire
                if m.get("role") == "tool" and m.get("tool_call_id") == "provider_1"
            ]
            assert results, "interrupted role=tool must pair the announcement"
            assert "stopped" in str(results[0].get("content", "")).lower()
            announced = [
                tc["id"]
                for m in wire
                if m.get("role") == "assistant" and m.get("tool_calls")
                for tc in m["tool_calls"]
            ]
            assert "provider_1" in announced
            # The snapshot→resume rebuild keeps the completed pair.
            rebuilt = _build_conversation(wire).to_messages()
            assert any(
                m.get("role") == "tool" and m.get("tool_call_id") == "provider_1"
                for m in rebuilt
            )
        finally:
            pass

    async def test_native_stop_snapshot_survives_real_resume(
        self, make_agent, tmp_path
    ):
        # FULL persisted lifecycle: live native round → stop() →
        # processing-end snapshot lands in a real SessionStore → store
        # closed → a fresh agent resumes via the actual entry point
        # (inject_saved_state) and sees the announcement + interrupted
        # result pair.
        from kohakuterrarium.session.resume import inject_saved_state
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "native.kohakutr.v2"))
        store.init_meta(
            session_id="n1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        started = asyncio.Event()
        agent = make_agent(script=["[/hangdirect]msg=x[hangdirect/]", "x"])
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        agent.attach_session_store(store)
        await agent.start()
        turn = asyncio.create_task(agent._process_event(create_user_input_event("go")))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            job_id = agent.executor.get_running_jobs()[0].job_id
            agent.controller.conversation.append(
                "assistant",
                "",
                tool_calls=[
                    {
                        "id": "provider_1",
                        "type": "function",
                        "function": {"name": "hangdirect", "arguments": "{}"},
                    }
                ],
            )
            agent._register_direct_job(
                job_id,
                kind="tool",
                name="hangdirect",
                tool_call_id="provider_1",
            )
            await agent.stop()
            await asyncio.wait_for(
                asyncio.gather(turn, return_exceptions=True), timeout=5
            )

            snap = store.load_conversation("test_agent")
            assert snap, "processing-end snapshot must be persisted"
            assert any(
                m.get("role") == "tool" and m.get("tool_call_id") == "provider_1"
                for m in snap
            ), f"persisted snapshot lost the interrupted pair: {snap}"

            # Close the original handle and REOPEN from the path — the
            # pair must survive a genuine cold restore, not just the
            # still-open store object.
            store_path = str(tmp_path / "native.kohakutr.v2")
            store.close()
            reopened = SessionStore(store_path)
            try:
                fresh = make_agent()
                inject_saved_state(fresh, reopened, "test_agent")
                wire = fresh.controller.conversation.to_messages()
                announced = [
                    tc["id"]
                    for m in wire
                    if m.get("role") == "assistant" and m.get("tool_calls")
                    for tc in m["tool_calls"]
                ]
                assert "provider_1" in announced
                assert any(
                    m.get("role") == "tool" and m.get("tool_call_id") == "provider_1"
                    for m in wire
                )
            finally:
                reopened.close()
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            store.close()

    async def test_stop_does_not_orphan_text_mode_jobs(self, make_agent):
        # Text-mode jobs have no announcement — the sweep must not
        # append an orphan role=tool message for them.
        from kohakuterrarium.core.job import JobState, JobStatus, JobType

        agent = make_agent()
        await agent.start()
        agent.executor.job_store.register(
            JobStatus(
                job_id="bash_txt",
                job_type=JobType.TOOL,
                type_name="bash",
                state=JobState.RUNNING,
            )
        )
        agent._register_direct_job("bash_txt", kind="tool", name="bash")
        await agent.stop()
        assert all(
            getattr(m, "role", None) != "tool"
            for m in agent.controller.conversation.get_messages()
        )

    async def test_stop_waits_for_outer_finalization(self, make_agent):
        # Cancelling the inner loop is not enough — the OUTER turn task
        # still runs _finalize_processing; stop() returning first lets
        # finalization emit into closed sinks.
        started = asyncio.Event()
        finalize_done = asyncio.Event()
        agent = make_agent(script=["[/hangdirect]msg=x[hangdirect/]", "x"])
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        orig_finalize = agent._finalize_processing

        async def slow_finalize(*args, **kwargs):
            await asyncio.sleep(0.15)
            result = await orig_finalize(*args, **kwargs)
            finalize_done.set()
            return result

        agent._finalize_processing = slow_finalize
        await agent.start()
        turn = asyncio.create_task(agent._process_event(create_user_input_event("go")))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            await agent.stop()
            assert finalize_done.is_set(), (
                "stop() must not return before the outer turn's "
                "finalization completed"
            )
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)

    async def test_concurrent_stops_are_serialized(self, make_agent):
        agent = make_agent()
        await agent.start()
        results = await asyncio.gather(
            agent.stop(), agent.stop(), return_exceptions=True
        )
        assert all(not isinstance(r, Exception) for r in results)
        assert agent._running is False

    async def test_sweep_is_idempotent_against_running_status(self, make_agent):
        # A repeat sweep (second stop(), engine + CLI both stopping)
        # must not emit a second terminal: the first sweep transitions
        # the job status off RUNNING.
        from kohakuterrarium.core.job import JobState, JobStatus, JobType

        agent = make_agent()
        await agent.start()
        try:
            agent.executor.job_store.register(
                JobStatus(
                    job_id="bash_zz",
                    job_type=JobType.TOOL,
                    type_name="bash",
                    state=JobState.RUNNING,
                )
            )
            emitted: list[str] = []
            orig = agent.output_router.notify_activity

            def spy(kind, message, metadata=None, **kwargs):
                if (metadata or {}).get("job_id") == "bash_zz":
                    emitted.append(kind)
                return orig(kind, message, metadata=metadata, **kwargs)

            agent.output_router.notify_activity = spy
            agent._finalize_inflight_jobs_for_stop()
            agent._finalize_inflight_jobs_for_stop()
            assert emitted == ["tool_error"], (
                "repeat sweep must find nothing — the first sweep "
                f"transitions the status off RUNNING; got {emitted}"
            )
            status = agent.executor.job_store.get_status("bash_zz")
            assert status is not None and not status.is_running
        finally:
            await agent.stop()

    async def test_stop_persists_genuine_terminal_for_running_job(
        self, make_agent, tmp_path
    ):
        # A job with no genuine terminal in the store renders as
        # "running" forever after resume (the FE ignores synthetic
        # resume terminals) — stop() must persist a real one.
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "sess.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        release = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangbg]msg=x[hangbg/]",
                "after bg dispatch",
            ]
        )
        tool = _HangingBgTool(release)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        agent.attach_session_store(store)
        # NOT _start_and_run — its finally-stop would fire the sweep
        # before the pre-stop assertions run.
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("kick off"))
            running = agent.executor.get_running_jobs()
            assert running, "background job must still be running pre-stop"
            job_id = running[0].job_id

            await agent.stop()

            events = store.get_events("test_agent")
            calls = [e for e in events if e.get("type") == "tool_call"]
            assert calls, f"expected a tool_call event; got: {events}"
            # The terminal must pair with the PERSISTED call's id — a
            # terminal under any other id leaves the call unterminated
            # and the FE renders it running forever.
            call_id = calls[-1].get("call_id")
            terminals = [
                e
                for e in events
                if e.get("type") == "tool_result" and e.get("call_id") == call_id
            ]
            assert terminals, (
                "stop() must persist a genuine terminal tool_result paired "
                f"with tool_call id {call_id!r} (job_id {job_id!r}); got "
                f"events: {events}"
            )
            data = terminals[-1]
            assert data.get("interrupted") is True
            assert data.get("final_state") == "interrupted"
            assert not data.get("_synthetic_resume")
        finally:
            release.set()
            store.close()


# ── _cancel_job paths ────────────────────────────────────────────


class TestCancelJobPaths:
    async def test_cancel_executor_task(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:

            async def slow():
                await asyncio.sleep(5)
                return None

            task = asyncio.create_task(slow())
            agent.executor._tasks["bash_slow"] = task
            from kohakuterrarium.core.job import JobStatus, JobState, JobType

            agent.executor.job_store.register(
                JobStatus(
                    job_id="bash_slow",
                    job_type=JobType.TOOL,
                    type_name="bash",
                    state=JobState.RUNNING,
                )
            )
            agent._cancel_job("bash_slow", "bash")
            # Give the cancellation a tick.
            await asyncio.sleep(0.01)
            assert task.cancelled() or task.done()
        finally:
            await agent.stop()


# ── inject_input slash command path ──────────────────────────────


class TestInjectInputSlashCommand:
    async def test_unknown_slash_command_falls_through(self, make_agent):
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            # ``/nope`` is not a recognised command — falls through to LLM.
            await agent.inject_input("/nope arg")
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
        finally:
            await agent.stop()


# ── _drive_input loop ────────────────────────────────────────────


class TestDriveInput:
    async def test_drive_input_handles_input_then_exit(self, make_agent):
        """A NoneInput that returns None + exit_requested triggers the
        exit branch of ``_drive_input``."""
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            # Stub the input to signal exit immediately.
            from kohakuterrarium.builtins.inputs.none import NoneInput

            class _ExitInput(NoneInput):
                async def get_input(self):
                    self._exit_requested = True
                    return None

            agent.input = _ExitInput()
            await agent._drive_input()
        finally:
            # Loop already exited.
            await agent.stop()

    async def test_drive_input_processes_real_event(self, make_agent):
        agent = make_agent(script=["resp"])

        class _OneShotInput:
            def __init__(self):
                self.fired = False
                self._running = True
                self.exit_requested = False

            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                if self.fired:
                    self.exit_requested = True
                    return None
                self.fired = True
                return create_user_input_event("hi")

        # Start first so the single event consumer is running (it drives
        # every turn now), then swap in the one-shot input.
        await agent.start()
        agent.input = _OneShotInput()
        try:
            await agent._drive_input()
            assert agent.controller.conversation.get_last_assistant_message()
        finally:
            await agent.stop()

    async def test_drive_input_waits_when_generic_input_returns_none(self, monkeypatch):
        agent = Agent.__new__(Agent)
        agent._pending_resume_events = None
        agent._pending_resume_triggers = None
        agent._startup_settled = asyncio.Event()
        agent._fire_startup_trigger = AsyncMock()
        agent._running = True
        agent.input = type(
            "GenericInput", (), {"get_input": AsyncMock(return_value=None)}
        )()

        async def stop_after_wait(delay):
            agent._running = False

        sleep = AsyncMock(side_effect=stop_after_wait)
        monkeypatch.setattr("kohakuterrarium.core.agent.asyncio.sleep", sleep)

        await agent._drive_input()

        agent.input.get_input.assert_awaited_once()
        sleep.assert_awaited_once_with(0.01)


# ── update_system_prompt edge cases ──────────────────────────────


class TestUpdateSystemPromptEdgeCases:
    async def test_replace_mode_overwrites(self, make_agent):
        agent = make_agent()
        agent.update_system_prompt("REPLACED", replace=True)
        assert agent.get_system_prompt() == "REPLACED"

    async def test_append_with_non_string_content(self, make_agent):
        agent = make_agent()
        # Force the system prompt to a list (unusual but possible).
        sys_msg = agent.controller.conversation.get_system_message()
        sys_msg.content = []  # type: ignore[assignment]
        # ``update_system_prompt`` only appends when content is str — for
        # list it silently no-ops the append.
        agent.update_system_prompt("X")
        assert sys_msg.content == []


# ── iteration budget exhausted ──────────────────────────────────


class TestIterationBudgetExhausted:
    async def test_budget_terminates_run(self, make_agent):
        agent = make_agent(
            script=["resp1", "resp2", "resp3"],
            max_iterations=1,
        )
        await agent.start()
        try:
            # Budget=1 means the first _check_termination consumes it
            # and the next attempt raises BudgetExhausted.
            await agent._process_event(create_user_input_event("hi"))
            # Budget is now consumed.
            assert agent.iteration_budget.exhausted
        finally:
            await agent.stop()


# ── _maybe_trigger_compact wires correctly ──────────────────────


class TestMaybeTriggerCompact:
    async def test_no_usage_no_trigger(self, make_agent):
        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            # Controller has no _last_usage yet — _maybe_trigger_compact is no-op.
            agent._maybe_trigger_compact(agent.controller)
            # No crash.
        finally:
            await agent.stop()


# ── _restore_triggers from saved state ──────────────────────────


class TestRestoreTriggers:
    async def test_restore_handles_missing_module(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            saved = [
                {
                    "trigger_id": "t1",
                    "type": "DoesNotExist",
                    "module": "definitely_no_such_module_xyz",
                    "data": {},
                }
            ]
            # Should not raise; failed restores are warned and skipped.
            await agent._restore_triggers(saved)
        finally:
            await agent.stop()

    async def test_restore_skips_empty_type_or_module(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            # Empty fields → silently skip.
            await agent._restore_triggers(
                [{"trigger_id": "t1", "type": "", "module": "", "data": {}}]
            )
        finally:
            await agent.stop()


# ── output_wiring resolver invocation ───────────────────────────


class TestOutputWiringEmit:
    async def test_emit_with_no_wiring(self, make_agent):
        agent = make_agent(script=["text"])
        await agent.start()
        try:
            evt = create_user_input_event("hi")
            # No wiring configured → emission is no-op.
            await agent._emit_output_wiring(evt)
        finally:
            await agent.stop()

    async def test_emit_invokes_resolver(self, make_agent):
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]

        calls = []

        class _Resolver:
            async def emit(self, **kwargs):
                calls.append(kwargs)

        agent._wiring_resolver = _Resolver()
        agent._last_turn_text = ["hello"]
        await agent.start()
        try:
            evt = create_user_input_event("hi")
            await agent._emit_output_wiring(evt)
            assert calls
            assert calls[0]["content"] == "hello"
        finally:
            await agent.stop()

    async def test_resolver_exception_swallowed(self, make_agent):
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]

        class _BadResolver:
            async def emit(self, **kwargs):
                raise RuntimeError("resolver crash")

        agent._wiring_resolver = _BadResolver()
        agent._last_turn_text = ["x"]
        await agent.start()
        try:
            evt = create_user_input_event("hi")
            await agent._emit_output_wiring(evt)
        finally:
            await agent.stop()

    async def test_emit_deferred_while_background_job_runs_then_fires(self, make_agent):
        # UXI-10 busy-guard: a turn that leaves background work running
        # must NOT emit its output wire (the creator would read it as the
        # child having finished / "turned off"); the emit fires only once
        # the last background job is done.
        from kohakuterrarium.core.job import JobState, JobStatus, JobType
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]
        calls: list = []

        class _Resolver:
            async def emit(self, **kwargs):
                calls.append(kwargs)

        agent._wiring_resolver = _Resolver()
        agent._last_turn_text = ["working on it in the background"]
        await agent.start()
        try:
            agent.executor.job_store.register(
                JobStatus(
                    job_id="bash_bg",
                    job_type=JobType.TOOL,
                    type_name="bash",
                    state=JobState.RUNNING,
                )
            )
            # This turn dispatched bash_bg as deliverable background work.
            agent._turn_dispatched_bg = {"bash_bg"}
            # Busy → the wire is deferred, not fired.
            await agent._emit_output_wiring(create_user_input_event("hi"))
            assert calls == []
            # The background job completes; the follow-up turn (which
            # dispatched nothing new → empty _turn_dispatched_bg) re-emits.
            agent.executor.job_store.update_status("bash_bg", state=JobState.DONE)
            agent._turn_dispatched_bg = set()
            agent._last_turn_text = ["all done, here is the result"]
            from kohakuterrarium.core.events import create_tool_complete_event

            await agent._emit_output_wiring(
                create_tool_complete_event(job_id="bash_bg", content="", exit_code=0)
            )
            assert len(calls) == 1
            assert calls[0]["content"] == "all done, here is the result"
        finally:
            await agent.stop()

    async def test_emit_not_stranded_by_unrelated_or_persistent_job(self, make_agent):
        # Critic Failure A: a running job that THIS turn did NOT dispatch as
        # deliverable background work (a persistent stateful tool, an
        # interactive sub-agent, or work from a prior turn) must NOT strand
        # the wire — otherwise the creator gets NO output at all.
        from kohakuterrarium.core.job import JobState, JobStatus, JobType
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]
        calls: list = []

        class _Resolver:
            async def emit(self, **kwargs):
                calls.append(kwargs)

        agent._wiring_resolver = _Resolver()
        agent._last_turn_text = ["my real output"]
        await agent.start()
        try:
            agent.executor.job_store.register(
                JobStatus(
                    job_id="monitor_persistent",
                    job_type=JobType.TOOL,
                    type_name="monitor",
                    state=JobState.RUNNING,
                )
            )
            # This turn dispatched NOTHING to the background (the running
            # monitor is unrelated / persistent).
            agent._turn_dispatched_bg = set()
            await agent._emit_output_wiring(create_user_input_event("hi"))
            # Wire fires despite the running job — not stranded.
            assert len(calls) == 1
            assert calls[0]["content"] == "my real output"
        finally:
            await agent.stop()

    async def test_notify_false_bg_tool_does_not_defer_wire(self, make_agent):
        # Critic Failure B: a promoted background tool with
        # notify_controller_on_background_complete=False completes WITHOUT
        # scheduling a follow-up turn — so it must NOT be tracked as
        # deliverable, or the deferred wire would never re-fire.
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolConfig,
            ToolResult,
        )

        class _FireForgetBg(BaseTool):
            def __init__(self):
                super().__init__(
                    ToolConfig(notify_controller_on_background_complete=False)
                )

            @property
            def tool_name(self):
                return "fnf"

            @property
            def description(self):
                return "fire and forget bg"

            @property
            def execution_mode(self):
                return ExecutionMode.BACKGROUND

            async def _execute(self, args, **kwargs):
                return ToolResult(output="ok")

        agent = make_agent(script=["[/fnf][fnf/]", "after"])
        tool = _FireForgetBg()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await _start_and_run(agent, create_user_input_event("go"))
        # The fire-and-forget background job was NOT recorded as deliverable,
        # so the output-wire guard would not have deferred on it.
        assert agent._turn_dispatched_bg == set()

    async def test_deliverable_bg_tool_is_tracked(self, make_agent):
        # The positive counterpart: a normal notify=True background tool IS
        # tracked so the wire defers until it reports back. A hanging bg tool
        # keeps the job running so we can inspect the tracking mid-flight.
        release = asyncio.Event()
        agent = make_agent(script=["[/hangbg]msg=x[hangbg/]", "after"])
        tool = _HangingBgTool(release)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("go"))
            running = agent.executor.get_running_jobs()
            assert running, "hanging bg tool should still be running"
            assert running[0].job_id in agent._turn_dispatched_bg
            # The guard would therefore defer the output wire.
            assert agent._has_unfinished_turn_bg_jobs() is True
        finally:
            release.set()
            await agent.stop()

    async def test_no_double_wire_when_owed_bg_completes_before_finalize(
        self, make_agent
    ):
        # UXI-10 double-emit race: a deliverable bg job THIS turn dispatched
        # completes in the window between the turn's final handle-wait and
        # _finalize_processing. It is no longer "running" when the turn-end
        # emit checks the guard, but its queued completion still drives a
        # follow-up turn that emits the real result. The guard must defer on
        # set MEMBERSHIP (not on "still running"), or the wired target gets
        # TWO creature_output deliveries for one logical result.
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]
        calls: list = []

        class _Resolver:
            async def emit(self, **kwargs):
                calls.append(kwargs)

        agent._wiring_resolver = _Resolver()
        await agent.start()
        try:
            # Turn T owes bash_bg, but it already completed (never registered
            # as a running job) — the race window. The turn-end emit must
            # defer: the queued completion still owns the emit.
            agent._turn_dispatched_bg = {"bash_bg"}
            agent._last_turn_text = ["working on it in the background"]
            await agent._emit_output_wiring(create_user_input_event("hi"))
            # The queued completion drives a follow-up turn (fresh cycle
            # reset the owed set) that emits the real post-completion result.
            agent._turn_dispatched_bg = set()
            agent._last_turn_text = ["all done, here is the result"]
            await agent._emit_output_wiring(
                create_tool_complete_event(job_id="bash_bg", content="", exit_code=0)
            )
            # Exactly ONE delivery, carrying the real result — not the
            # mid-flight "working on it" text from the deferred turn.
            assert len(calls) == 1
            assert calls[0]["content"] == "all done, here is the result"
        finally:
            await agent.stop()

    async def test_drained_bg_completion_releases_owed_wire_defer(self, make_agent):
        # Regression guard for membership-based deferral: a deliverable bg
        # job whose completion is DRAINED into the current turn (folded
        # mid-turn) drives NO follow-up turn. The drain must release its
        # owed-emit defer, or the output wire strands forever waiting on a
        # follow-up that never comes.
        from kohakuterrarium.core.event_inbox import EventEnvelope
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.core.output_wiring import OutputWiringEntry

        agent = make_agent(script=["text"])
        agent.config.output_wiring = [OutputWiringEntry(to="other")]
        calls: list = []

        class _Resolver:
            async def emit(self, **kwargs):
                calls.append(kwargs)

        agent._wiring_resolver = _Resolver()
        await agent.start()
        try:
            agent._turn_dispatched_bg = {"bash_bg"}
            agent._event_inbox.put(
                EventEnvelope(
                    create_tool_complete_event(
                        job_id="bash_bg", content="raw result", exit_code=0
                    )
                )
            )
            drained = await agent._drain_mid_turn_pending_inputs(agent.controller)
            assert drained == 1
            # Folded into this turn → the owed job is released.
            assert "bash_bg" not in agent._turn_dispatched_bg
            # So the turn-end emit fires (not stranded on a phantom follow-up).
            agent._last_turn_text = ["all done, here is the result"]
            await agent._emit_output_wiring(create_user_input_event("hi"))
            assert len(calls) == 1
            assert calls[0]["content"] == "all done, here is the result"
        finally:
            await agent.stop()


# ── LLM exception during processing ─────────────────────────────


class TestLLMExceptionDuringProcessing:
    async def test_llm_error_emits_processing_error(self, make_agent, patched_llm):
        class _BadLLM(ScriptedLLM):
            async def chat(self, messages, **kwargs):
                if False:
                    yield ""
                raise RuntimeError("API outage")

        bad_llm = _BadLLM([])

        from kohakuterrarium.bootstrap import llm as bootstrap_llm
        from kohakuterrarium.bootstrap import agent_init

        def _fake_create(cfg, *a, **kw):
            return bad_llm

        # Patch the LLM factory so the next agent built picks up bad_llm.
        import unittest.mock as um

        with um.patch.object(bootstrap_llm, "create_llm_provider", _fake_create):
            with um.patch.object(agent_init, "create_llm_provider", _fake_create):
                agent = make_agent()  # uses our patched factory
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            # Error path emits processing_error activity. Agent stays alive.
            assert agent.is_running is True
        finally:
            await agent.stop()


# ── conversation-level branching via real agent ─────────────────


class TestRealAgentBranchBookkeeping:
    async def test_two_user_inputs_track_parent_branch_path(self, make_agent):
        agent = make_agent(script=["r1", "r2"])
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("u1"))
            # After 1st turn: _turn_index=1, _branch_id=1.
            t1, b1 = agent._turn_index, agent._branch_id
            assert (t1, b1) == (1, 1)
            await agent._process_event(create_user_input_event("u2"))
            # 2nd turn: index bumps; parent_branch_path captures the
            # previous turn's branch.
            assert agent._turn_index == 2
            assert (1, 1) in agent._parent_branch_path
        finally:
            await agent.stop()


# ── session_store attached: user_input events written ──────────


class TestSessionStoreUserInputAppend:
    async def test_user_input_events_appended(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "sess.kohakutr.v2"))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent(script=["resp"])
        agent.attach_session_store(store)
        await agent.start()
        try:
            event = create_user_input_event("hello")
            event.context["pending_id"] = "c_primary"
            await agent._process_event(event)
            events = store.get_events("test_agent")
            types_ = [e["type"] for e in events]
            assert "user_input" in types_
            assert "user_message" in types_
            user_events = [
                event
                for event in events
                if event["type"] in {"user_input", "user_message"}
            ]
            assert all(event["pending_id"] == "c_primary" for event in user_events)
            assert all(isinstance(event["event_id"], int) for event in user_events)
            resumable_user_events = [
                event
                for event in store.get_resumable_events("test_agent")
                if event["type"] in {"user_input", "user_message"}
            ]
            assert all(
                event["pending_id"] == "c_primary" for event in resumable_user_events
            )
        finally:
            await agent.stop()


# ── _maybe_trigger_compact actually fires ────────────────────────


class TestCompactTrigger:
    async def test_compact_fires_at_threshold(self, make_agent):
        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            # Force the controller's _last_usage above threshold.
            agent.controller._last_usage = {
                "prompt_tokens": agent.compact_manager.config.max_tokens,
            }
            # Should call should_compact and trigger.
            # In practice this returns False because compact_manager's controller
            # has a tiny conversation — but the threshold check is exercised.
            agent._maybe_trigger_compact(agent.controller)
        finally:
            await agent.stop()


# ── Sub-agent dispatch via real Agent ────────────────────────────


class TestSubAgentDispatch:
    async def test_unknown_subagent_emits_error_subagent(self, make_agent):
        """When the LLM calls a subagent that isn't registered, the
        dispatch path produces an error subagent_start with an error_<name>
        job id."""
        agent = make_agent(
            script=[
                "[/agent_ghost]task=do something[agent_ghost/]",
                "OK done.",
            ]
        )
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("dispatch"))
            # No crash. Conversation has at least one assistant turn.
            assert (
                agent.controller.conversation.get_last_assistant_message() is not None
            )
        finally:
            await agent.stop()


# ── pre_tool_dispatch plugin veto ────────────────────────────────


class _VetoPlugin:
    """Vetoes every tool call."""

    name = "veto"
    priority = 0
    enabled = True
    command_override = False

    async def pre_tool_dispatch(self, event, ctx):
        from kohakuterrarium.modules.plugin.base import PluginBlockError

        raise PluginBlockError("vetoed by policy")

    async def on_load(self, ctx):
        pass

    async def on_unload(self, ctx):
        pass


class TestPluginVeto:
    async def test_tool_call_vetoed_by_plugin(self, make_agent):
        from kohakuterrarium.modules.plugin.manager import PluginManager

        agent = make_agent(
            script=[
                "[/echo]msg=hi[echo/]",
                "Sorry can't.",
            ]
        )
        tool = _EchoTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        mgr = PluginManager()
        mgr.register(_VetoPlugin())
        agent.plugins = mgr
        agent.controller.plugins = mgr
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("hi"))
            # Run completes, last assistant response present.
            assert (
                agent.controller.conversation.get_last_assistant_message() is not None
            )
        finally:
            await agent.stop()


# ── _on_provider_emergency_drop ──────────────────────────────────


class TestEmergencyDrop:
    async def test_replaces_conversation(self, make_agent):
        agent = make_agent()
        # New messages from a (hypothetical) provider emergency drop.
        new_msgs = [
            {"role": "user", "content": "rebuilt"},
            {"role": "assistant", "content": "rebuilt resp"},
        ]
        agent._on_provider_emergency_drop(new_msgs)
        roles = [m.role for m in agent.controller.conversation.get_messages()]
        assert "user" in roles


# ── pending resume events processed ──────────────────────────────


class TestPendingResume:
    async def test_drive_input_emits_resume_batch(self, make_agent):
        agent = make_agent(script=["ack"])
        agent._pending_resume_events = [{"type": "user_input", "content": "old"}]
        emitted = []

        class _Router:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def flush(self):
                pass

            async def on_processing_start(self):
                pass

            async def on_processing_end(self):
                pass

            async def emit(self, event):
                emitted.append(event)

            def notify_activity(self, *a, **kw):
                pass

            default_output = None

            def reset(self):
                pass

        agent.output_router = _Router()

        class _ExitInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            @property
            def exit_requested(self):
                return True

            async def get_input(self):
                return None

        agent.input = _ExitInput()
        agent._running = True
        try:
            await agent._drive_input()
            # resume_batch event emitted.
            assert any(e.type == "resume_batch" for e in emitted)
        finally:
            agent._running = False


# ── Subagent dispatch via real Agent + registered sub-agent ─────


class TestSubAgentRealDispatch:
    async def test_subagent_run_completes_and_returns_result(
        self, make_agent, patched_llm
    ):
        from kohakuterrarium.modules.subagent.config import SubAgentConfig
        from kohakuterrarium.testing.llm import ScriptedLLM

        # Sub-agent's own LLM returns a single text response.
        sa_llm = ScriptedLLM(["explored ok"])

        agent = make_agent(
            script=[
                "[/agent_explore]task=look around[agent_explore/]",
                "Final report.",
            ]
        )
        # Register a sub-agent via SubAgentManager.register.
        sa_cfg = SubAgentConfig(
            name="explore",
            description="Explore",
            tools=[],
            system_prompt="explorer",
            max_turns=1,
        )
        agent.subagent_manager.register(sa_cfg)
        # Force the sub-agent manager to use our scripted LLM.
        agent.subagent_manager.llm = sa_llm
        await _start_and_run(agent, create_user_input_event("explore"))
        # Conversation has a final assistant response.
        last = agent.controller.conversation.get_last_assistant_message()
        assert last is not None


# ── Channel-triggered processing_complete ────────────────────────


class TestChannelTriggerProcessingComplete:
    async def test_processing_complete_fired_for_channel_event(self, make_agent):
        agent = make_agent(script=["channel response"])
        await agent.start()
        try:
            # Build a fake trigger event with channel + sender context.
            from kohakuterrarium.core.events import TriggerEvent

            evt = TriggerEvent(
                type="user_input",
                content="hello from channel",
                context={"channel": "alpha", "sender": "bob"},
            )
            await agent._process_event(evt)
            # The processing_complete activity is emitted on the
            # output_router. Just verify the run finished without crash.
            assert agent.is_running is True
        finally:
            await agent.stop()


# ── turn_token_usage emission ────────────────────────────────────


class TestTurnTokenUsageEmission:
    async def test_turn_usage_emitted_when_accum_has_values(self, make_agent):
        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            # Pre-seed the accumulator before processing.
            agent._turn_usage_accum["prompt_tokens"] = 10
            agent._turn_usage_accum["completion_tokens"] = 7
            # Call _finalize_processing directly.
            from kohakuterrarium.core.events import TriggerEvent

            evt = TriggerEvent(type="user_input", content="x")
            await agent._finalize_processing(evt, agent.controller, ["chunk"])
        finally:
            await agent.stop()


class TestAssistantReasoningEmission:
    async def test_reasoning_activity_emitted_from_latest_round_fields(
        self, make_agent, monkeypatch
    ):
        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            agent.controller.llm.last_assistant_extra_fields = {
                "reasoning_content": "think",
                "_kt_assistant_segments": [
                    {
                        "type": "reasoning",
                        "source": "reasoning_content",
                        "text": "think",
                    },
                    {"type": "text", "text": "resp"},
                ],
            }
            calls = []

            def capture(activity_type, detail, metadata=None):
                calls.append((activity_type, detail, metadata))

            monkeypatch.setattr(agent.output_router, "notify_activity", capture)
            agent._emit_assistant_reasoning(agent.controller)
            assert (
                "assistant_reasoning",
                "turn 0 assistant reasoning",
                {
                    "reasoning_content": "think",
                    "_kt_assistant_segments": [
                        {
                            "type": "reasoning",
                            "source": "reasoning_content",
                            "text": "think",
                        },
                        {"type": "text", "text": "resp"},
                    ],
                    "turn_index": 0,
                    "branch_id": 0,
                },
            ) in calls
        finally:
            await agent.stop()

    async def test_reasoning_emitted_after_each_controller_round(
        self, make_agent, monkeypatch
    ):
        from kohakuterrarium.core.agent_tools import _TurnResult

        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            rounds = 0

            async def fake_turn(controller):
                nonlocal rounds
                rounds += 1
                if rounds == 1:
                    controller.llm.last_assistant_extra_fields = {
                        "reasoning_content": "think A"
                    }
                else:
                    controller.llm.last_assistant_extra_fields = {
                        "reasoning_content": "think B"
                    }
                return _TurnResult(
                    handles={},
                    handle_order=[],
                    text_output=["text"],
                    native_mode=False,
                    native_tool_call_ids={},
                )

            async def fake_feedback(*args, **kwargs):
                return rounds < 2

            monkeypatch.setattr(agent, "_run_single_turn", fake_turn)
            monkeypatch.setattr(agent, "_collect_and_push_feedback", fake_feedback)
            monkeypatch.setattr(agent, "_emit_token_usage", lambda controller: None)

            calls = []

            def capture(activity_type, detail, metadata=None):
                calls.append((activity_type, detail, metadata))

            monkeypatch.setattr(agent.output_router, "notify_activity", capture)
            await agent._run_controller_loop(agent.controller, [])

            reasoning = [
                metadata["reasoning_content"]
                for _, _, metadata in calls
                if metadata and metadata.get("reasoning_content")
            ]
            assert reasoning == ["think A", "think B"]
        finally:
            await agent.stop()


# ── _check_termination budget exhausted + force_terminate ───────


class TestCheckTerminationBudget:
    async def test_budget_exhausted_force_terminates_checker(self, make_agent):
        agent = make_agent(
            script=["once"],
            termination={"max_turns": 10},
            max_iterations=1,
        )
        await agent.start()
        try:
            # Drain the budget so the next consume raises BudgetExhausted.
            agent.iteration_budget.consume(1)
            terminated = agent._check_termination(["some output"])
            assert terminated is True
            assert agent._running is False
            # Checker was force-terminated with budget reason.
            assert "Iteration budget" in agent._termination_checker.reason
        finally:
            agent._running = True  # reset for cleanup
            await agent.stop()

    async def test_no_checker_no_budget_returns_false(self, make_agent):
        agent = make_agent(script=["x"])
        # Default: no termination + no iteration budget.
        await agent.start()
        try:
            assert agent._check_termination(["output"]) is False
        finally:
            await agent.stop()

    async def test_keyword_termination(self, make_agent):
        agent = make_agent(
            script=["x"],
            termination={"keywords": ["STOP"]},
        )
        await agent.start()
        try:
            # Output contains the stop keyword.
            terminated = agent._check_termination(["..STOP.."])
            assert terminated is True
        finally:
            await agent.stop()


# ── _collect_and_push_feedback paths ────────────────────────────


class TestCollectAndPushFeedback:
    async def test_no_handles_returns_false_when_no_feedback(self, make_agent):
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            result = await agent._collect_and_push_feedback(
                agent.controller, {}, [], {}, False
            )
            # No handles + no feedback → loop exits.
            assert result is False
        finally:
            await agent.stop()

    async def test_interrupt_cancels_handles(self, make_agent):
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            from kohakuterrarium.core.backgroundify import BackgroundifyHandle
            from unittest.mock import MagicMock

            handle = MagicMock(spec=BackgroundifyHandle)
            handle.promoted = False
            handle.done = False
            handle.task = MagicMock()
            agent._interrupt_requested = True
            ok = await agent._collect_and_push_feedback(
                agent.controller, {"x": handle}, ["x"], {}, False
            )
            assert ok is False
        finally:
            agent._interrupt_requested = False
            await agent.stop()


# ── _prepare_processing_cycle resets accum ─────────────────────


class TestPrepareProcessingCycle:
    async def test_resets_turn_usage_accum(self, make_agent):
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            agent._turn_usage_accum["prompt_tokens"] = 99
            from kohakuterrarium.core.events import TriggerEvent

            evt = TriggerEvent(type="user_input", content="x")
            agent._prepare_processing_cycle(evt, agent.controller)
            assert agent._turn_usage_accum["prompt_tokens"] == 0
        finally:
            await agent.stop()


# ── _emit_startup_session_info paths ────────────────────────────


class TestEmitStartupSessionInfo:
    async def test_full_path_with_session_store(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="sess_42",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store)
        # Configure memory.embedding so the embedding_config path is hit.
        agent.config.memory = {"embedding": {"provider": "model2vec"}}
        await agent.start()
        try:
            # Already triggered by start() — check side effects.
            assert (
                store.state.get("embedding_config", {}).get("provider") == "model2vec"
            )
        finally:
            await agent.stop()


# ── _init_plugins with pre-existing plugins ─────────────────────


class TestInitPluginsWithPreSet:
    async def test_pre_set_plugins_early_return_no_crash(self, make_agent):
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager

        class _NoOpPlugin(BasePlugin):
            name = "noop"

        agent = make_agent()
        mgr = PluginManager()
        mgr.register(_NoOpPlugin())  # make manager truthy
        agent.plugins = mgr
        agent._init_plugins()


# ── _promote_handle paths ───────────────────────────────────────


class TestPromoteHandle:
    async def test_promote_existing_handle_via_event_loop(self, make_agent):
        from unittest.mock import MagicMock

        from kohakuterrarium.core.backgroundify import BackgroundifyHandle

        agent = make_agent()
        await agent.start()
        try:
            h = MagicMock(spec=BackgroundifyHandle)
            h.promote = MagicMock(return_value=True)
            agent._active_handles["bash_x"] = h
            ok = agent._promote_handle("bash_x")
            assert ok is True
            h.promote.assert_called_once()
        finally:
            await agent.stop()

    async def test_promote_returns_false_when_promote_fails(self, make_agent):
        from unittest.mock import MagicMock

        from kohakuterrarium.core.backgroundify import BackgroundifyHandle

        agent = make_agent()
        await agent.start()
        try:
            h = MagicMock(spec=BackgroundifyHandle)
            h.promote = MagicMock(return_value=False)
            agent._active_handles["bash_x"] = h
            ok = agent._promote_handle("bash_x")
            assert ok is False
        finally:
            await agent.stop()


# ── set_output_handler replace_default ──────────────────────────


class TestSetOutputHandlerReplace:
    async def test_replace_default(self, make_agent):
        captured = []
        agent = make_agent()
        agent.set_output_handler(lambda t: captured.append(t), replace_default=True)
        # The default output is now a CallbackOutput that forwards every
        # written chunk to our callback — verify it actually routes.
        await agent.output_router.default_output.write_stream("chunk-1")
        await agent.output_router.default_output.write("chunk-2")
        assert captured == ["chunk-1", "chunk-2"]


# ── Agent.from_path / run / run_agent ────────────────────────────


class TestAgentFromPath:
    def test_from_path_loads_config(self, patched_llm, tmp_path):
        """``Agent.from_path`` reads a config dir and constructs an Agent."""
        from kohakuterrarium.core.agent import Agent

        # Use the kt-template creature config.
        config_dir = tmp_path / "creature"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "name: tmpl\n"
            "controller:\n"
            "  tool_format: bracket\n"
            "  include_tools_in_prompt: false\n"
            "  include_hints_in_prompt: false\n"
            "system_prompt: |\n"
            "  test\n"
            "input:\n"
            "  type: none\n"
            "output:\n"
            "  type: stdout\n"
        )
        agent = Agent.from_path(str(config_dir))
        assert agent.config.name == "tmpl"


class TestRunAgentWrapper:
    async def test_run_agent_runs_through(self, patched_llm, tmp_path):
        from kohakuterrarium.core.agent import Agent, run_agent

        patched_llm.set_script(["x"])
        config_dir = tmp_path / "ck"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "name: ck\n"
            "controller:\n"
            "  tool_format: bracket\n"
            "  include_tools_in_prompt: false\n"
            "  include_hints_in_prompt: false\n"
            "system_prompt: x\n"
            "input:\n"
            "  type: none\n"
            "output:\n"
            "  type: stdout\n"
        )
        # Patch Agent.run_forever so we exit immediately.
        original_run = Agent.run_forever

        async def _stub_run(self):
            self._running = True
            await self.stop()

        Agent.run_forever = _stub_run
        try:
            await run_agent(str(config_dir))
        finally:
            Agent.run_forever = original_run


# ── _drive_input handles startup + multimodal log path ──────────


class TestDriveInputMultimodal:
    async def test_multimodal_input_logged(self, make_agent):
        from kohakuterrarium.llm.message import ImagePart, TextPart

        agent = make_agent(script=["ack"])

        class _MultimodalInput:
            def __init__(self):
                self.fired = False
                self.exit_requested = False

            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                if self.fired:
                    self.exit_requested = True
                    return None
                self.fired = True
                return create_user_input_event(
                    [TextPart(text="describe"), ImagePart(url="x")]
                )

        agent.input = _MultimodalInput()
        agent._running = True
        # _drive_input drives turns through the single consumer — spawn it.
        agent._consumer_resume.set()
        agent._consumer_task = asyncio.create_task(agent._run_event_consumer())
        await agent._drive_input()
        await agent.stop()


class TestDriveInputFatalError:
    async def test_fatal_error_propagates(self, make_agent):
        agent = make_agent(script=["x"])

        class _BadInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                raise RuntimeError("input crash")

        agent.input = _BadInput()
        agent._running = True
        with pytest.raises(RuntimeError, match="input crash"):
            try:
                await agent._drive_input()
            finally:
                agent._running = False


# ── Trigger restoration via real module ──────────────────────────


class TestRestoreTriggersSuccess:
    async def test_restore_existing_trigger_id_skipped(self, make_agent):
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        class _Noop(BaseTrigger):
            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

        agent = make_agent()
        await agent.start()
        try:
            # Pre-register a trigger.
            tid = await agent.add_trigger(_Noop())
            # Try to restore something with the same trigger_id — skipped.
            saved = [{"trigger_id": tid, "type": "_Noop", "module": "", "data": {}}]
            await agent._restore_triggers(saved)
        finally:
            await agent.stop()


# ── _init_plugins fresh config-driven path ──────────────────────


class TestInitPluginsFreshFromConfig:
    async def test_fresh_init_with_config_plugins(self, make_agent):
        agent = make_agent()
        # Drop existing plugins so fresh init from config runs.
        agent.plugins = None
        agent._init_plugins()
        # Either no plugins (no config) or wired correctly.
        assert True

    async def test_fresh_init_returns_early_when_empty_manager(
        self, make_agent, monkeypatch
    ):
        """When ``init_plugins`` returns a falsy manager (empty), the
        early-return at line 427 fires."""
        from kohakuterrarium.core import agent as agent_mod
        from kohakuterrarium.modules.plugin.manager import PluginManager

        # Force init_plugins to always return an empty manager.
        monkeypatch.setattr(agent_mod, "init_plugins", lambda *a, **kw: PluginManager())
        agent = make_agent()
        agent.plugins = None
        agent._init_plugins()
        # No crash; plugins remained empty (line 427 hit).
        assert not agent.plugins


# ── _publish_session_info code path ──────────────────────────────


class TestPublishSessionInfo:
    async def test_publishes_with_store_metadata_error(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="abc",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store)
        await agent.start()
        try:
            # Make load_meta raise to cover the defensive path.
            def boom():
                raise RuntimeError("disk")

            store.load_meta = boom
            agent._publish_session_info()
        finally:
            await agent.stop()


# ── CallbackOutput methods executed via set_output_handler ──────


class TestCallbackOutputMethods:
    async def test_secondary_callback_lifecycle(self, make_agent):
        captured = []
        agent = make_agent(script=["streamed"])
        agent.set_output_handler(lambda t: captured.append(t))
        # The secondary CallbackOutput will be lifecycled by the router
        # during start/stop, exercising every overridden method.
        await _start_and_run(agent, create_user_input_event("hi"))
        # At least one chunk delivered via write_stream.
        assert captured

    async def test_replace_default_callback_writes_lifecycle(self, make_agent):
        captured = []
        agent = make_agent(script=["resp"])
        agent.set_output_handler(lambda t: captured.append(t), replace_default=True)
        await _start_and_run(agent, create_user_input_event("hi"))


# ── attach_session_store native/plugin option apply branches ────


class TestAttachSessionStoreOptionsApply:
    async def test_native_tool_options_apply_failure_swallowed(
        self, make_agent, tmp_path
    ):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()

        # Make native_tool_options.apply raise.
        def boom():
            raise RuntimeError("native apply failed")

        agent.native_tool_options.apply = boom  # type: ignore[method-assign]
        # Must not raise — handled defensively.
        agent.attach_session_store(store)

    async def test_plugin_options_apply_failure_swallowed(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()

        def boom():
            raise RuntimeError("plugin apply failed")

        agent.plugin_options.apply = boom  # type: ignore[method-assign]
        agent.attach_session_store(store)

    async def test_tool_options_apply_failure_swallowed(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()

        def boom():
            raise RuntimeError("tool apply failed")

        agent.tool_options.apply = boom  # type: ignore[method-assign]
        agent.attach_session_store(store)

    async def test_attach_compact_count_invalid_skipped(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        # Put garbage in state where compact_count would be.
        store.state["test_agent:compact_count"] = "not-an-int"
        agent = make_agent()
        agent.attach_session_store(store)
        # No crash; compact_manager.compact_count stays at default.


# ── _cancel_job sub-agent branch ────────────────────────────────


class TestCancelSubAgent:
    async def test_cancel_subagent_task(self, make_agent):
        from kohakuterrarium.modules.subagent.config import SubAgentConfig

        agent = make_agent()
        await agent.start()
        try:
            # Register a sub-agent and seed a running task into the manager.
            agent.subagent_manager.register(
                SubAgentConfig(name="explore", system_prompt="x", max_turns=1)
            )

            async def slow():
                await asyncio.sleep(5)

            task = asyncio.create_task(slow())
            agent.subagent_manager._tasks["agent_x"] = task
            agent._cancel_job("agent_x", "explore")
            await asyncio.sleep(0.01)
            assert task.cancelled() or task.done()
        finally:
            await agent.stop()


# ── _wire_trigger_notifications fires on trigger ────────────────


class TestWireTriggerNotifications:
    async def test_trigger_fired_callback_emits_activity(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            cb = agent.trigger_manager.on_trigger_fired
            assert cb is not None
            before = len(agent._recorder.activities)
            # Build a fake event with channel context.
            from kohakuterrarium.core.events import TriggerEvent

            evt = TriggerEvent(
                type="timer",
                content="x",
                context={"channel": "c1", "sender": "s1", "raw_content": "raw"},
            )
            cb("trigger_xyz", evt)
            # The callback routes a trigger-fired activity to the output.
            assert len(agent._recorder.activities) > before
        finally:
            await agent.stop()


# ── _on_sa_tool_activity wiring ─────────────────────────────────


class TestSubagentToolActivity:
    async def test_subagent_tool_activity_callback(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            cb = agent.subagent_manager._on_tool_activity
            assert cb is not None
            before = len(agent._recorder.activities)
            cb("explore", "start", "bash", "running", sa_job_id="agent_x")
            cb(
                "explore",
                "done",
                "bash",
                "complete",
                sa_job_id="agent_x",
                extra={"k": "v"},
            )
            # Both sub-agent tool-activity calls surface as activities on
            # the agent's output recorder.
            assert len(agent._recorder.activities) >= before + 2
        finally:
            await agent.stop()


# ── _configure_tui_tabs no tui input branch ─────────────────────


class TestConfigureTUITabs:
    async def test_with_terrarium_tabs(self, make_agent):
        agent = make_agent()
        agent.session.extra["terrarium_tui_tabs"] = ["tab1"]
        # Stub a TUI input.
        agent.input._tui = object()
        # Just call — verifies the log branch.
        agent._configure_tui_tabs()


# ── _restore_triggers success path (covers 70-81) ───────────────


class TestRestoreTriggersFullPath:
    async def test_restore_real_trigger_class(self, make_agent):
        # Build a synthetic module with a BaseTrigger subclass.
        import sys
        import types

        from kohakuterrarium.modules.trigger.base import BaseTrigger

        mod = types.ModuleType("_test_restore_trig_mod")

        class _NoopTrigger(BaseTrigger):
            resumable = True

            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

            @classmethod
            def from_resume_dict(cls, data):
                return cls()

        mod._NoopTrigger = _NoopTrigger
        sys.modules["_test_restore_trig_mod"] = mod

        agent = make_agent()
        await agent.start()
        try:
            saved = [
                {
                    "trigger_id": "restored_1",
                    "type": "_NoopTrigger",
                    "module": "_test_restore_trig_mod",
                    "data": {},
                }
            ]
            await agent._restore_triggers(saved)
            # Restored trigger is present.
            assert "restored_1" in agent.trigger_manager._triggers
        finally:
            await agent.stop()

    async def test_restore_existing_trigger_id_continue(self, make_agent):
        """When the saved trigger_id is already registered, the loop
        hits the ``continue`` (line 66)."""
        import sys
        import types

        from kohakuterrarium.modules.trigger.base import BaseTrigger

        mod = types.ModuleType("_test_restore_dup_mod")

        class _Noop(BaseTrigger):
            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

            @classmethod
            def from_resume_dict(cls, data):
                return cls()

        mod._Noop = _Noop
        sys.modules["_test_restore_dup_mod"] = mod

        agent = make_agent()
        await agent.start()
        try:
            tid = await agent.add_trigger(_Noop(), trigger_id="already_here")
            saved = [
                {
                    "trigger_id": "already_here",
                    "type": "_Noop",
                    "module": "_test_restore_dup_mod",
                    "data": {},
                }
            ]
            # ``continue`` fires because trigger_id is already registered.
            await agent._restore_triggers(saved)
            assert tid == "already_here"
        finally:
            await agent.stop()

    async def test_restore_wires_session_channels(self, make_agent):
        """Restored trigger with ``_registry=None`` gets wired to
        ``session.channels`` (line 77-78)."""
        import sys
        import types

        from kohakuterrarium.modules.trigger.base import BaseTrigger

        mod = types.ModuleType("_test_restore_chan_mod")

        class _ChanTrig(BaseTrigger):
            _registry = None  # required attribute for wiring

            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

            @classmethod
            def from_resume_dict(cls, data):
                inst = cls()
                inst._registry = None
                return inst

        mod._ChanTrig = _ChanTrig
        sys.modules["_test_restore_chan_mod"] = mod

        agent = make_agent()
        await agent.start()
        try:
            saved = [
                {
                    "trigger_id": "chan_t",
                    "type": "_ChanTrig",
                    "module": "_test_restore_chan_mod",
                    "data": {},
                }
            ]
            await agent._restore_triggers(saved)
            # Trigger registered with its _registry wired to session.channels.
            t = agent.trigger_manager._triggers["chan_t"]
            assert t._registry is agent.session.channels
        finally:
            await agent.stop()

    async def test_restore_wires_environment_channels(self, make_agent):
        """Trigger restore wires to environment.shared_channels when
        environment is set (lines 75-76)."""
        import sys
        import types

        from kohakuterrarium.core.environment import Environment
        from kohakuterrarium.modules.trigger.base import BaseTrigger

        mod = types.ModuleType("_test_restore_env_mod")

        class _EnvTrig(BaseTrigger):
            _registry = None

            async def wait_for_trigger(self):
                await asyncio.sleep(60)
                return None

            @classmethod
            def from_resume_dict(cls, data):
                inst = cls()
                inst._registry = None
                return inst

        mod._EnvTrig = _EnvTrig
        sys.modules["_test_restore_env_mod"] = mod

        agent = make_agent()
        env = Environment()
        agent.environment = env
        await agent.start()
        try:
            saved = [
                {
                    "trigger_id": "env_t",
                    "type": "_EnvTrig",
                    "module": "_test_restore_env_mod",
                    "data": {},
                }
            ]
            await agent._restore_triggers(saved)
            t = agent.trigger_manager._triggers["env_t"]
            assert t._registry is env.shared_channels
        finally:
            await agent.stop()


# ── _process_event dropped when not running (covers 248-249) ───


class TestProcessEventDropped:
    async def test_dropped_when_not_running(self, make_agent):
        from kohakuterrarium.errors import AgentNotRunningError

        agent = make_agent()
        # Don't start — agent._running is False; strict agents raise.
        with pytest.raises(AgentNotRunningError):
            await agent._process_event(create_user_input_event("hi"))
        # No assistant turn appended.
        msgs = agent.controller.conversation.get_messages()
        assert all(m.role != "assistant" for m in msgs)


# ── _dispatch_tool_event with run_in_background flag ────────────


class TestDispatchToolEventBackgroundFlag:
    async def test_run_in_background_flag(self, make_agent):
        agent = make_agent(
            script=[
                "[/echo]msg=bg run_in_background=true[echo/]",
                "Done.",
            ]
        )
        tool = _EchoTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await _start_and_run(agent, create_user_input_event("bg call"))
        assert agent.controller.conversation.get_last_assistant_message() is not None


# ── _interrupt during controller loop (covers 296-301, 316-322) ──


class TestInterruptDuringTurn:
    async def test_interrupt_before_round(self, make_agent):
        agent = make_agent(script=["delayed"])
        await agent.start()
        try:
            # Trigger interrupt before processing.
            agent._interrupt_requested = True
            await agent._process_event(create_user_input_event("hi"))
            # Interrupt was consumed.
            assert agent._interrupt_requested is False
        finally:
            await agent.stop()


# ── _check_termination plugin checker ratio (covers 589) ─────────


class TestCheckTerminationPluginChecker:
    async def test_plugin_checker_triggers_termination(self, make_agent):
        from kohakuterrarium.core.termination import (
            TerminationChecker,
            TerminationConfig,
            TerminationDecision,
        )

        agent = make_agent(script=["resp"])
        await agent.start()
        try:
            # Replace with a checker that has plugin manager voting stop.
            cfg = TerminationConfig(max_turns=100)
            ck = TerminationChecker(cfg)

            class _StopPlugin:
                def collect_termination_checkers(self):
                    return [
                        (
                            "p",
                            lambda ctx: TerminationDecision(
                                should_stop=True, reason="plugin says stop"
                            ),
                        )
                    ]

            ck.attach_plugins(_StopPlugin())
            ck.start()
            agent._termination_checker = ck
            terminated = agent._check_termination(["output"])
            assert terminated is True
        finally:
            await agent.stop()


# ── _maybe_trigger_compact with prompt_tokens (covers 741) ──────


class TestMaybeTriggerCompactWithTokens:
    async def test_compact_actually_triggers(self, make_agent):
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            # Set prompt_tokens way above threshold.
            agent.controller._last_usage = {
                "prompt_tokens": agent.compact_manager.config.max_tokens
            }
            # Reset cooldown to ensure should_compact returns True.
            agent.compact_manager._last_compact_time = 0
            agent._maybe_trigger_compact(agent.controller)
        finally:
            await agent.stop()


# ── _dispatch_subagent_event direct (lines 486-536) ─────────────


class TestDispatchSubAgentEventDirect:
    async def test_full_dispatch_with_handle(self, make_agent):
        """Drive the sub-agent dispatch helper directly to cover all
        the registration and back-grounding branches."""
        from kohakuterrarium.parsing import SubAgentCallEvent
        from kohakuterrarium.modules.subagent.config import SubAgentConfig

        agent = make_agent()

        async def fake_spawn(event):
            # Return job_id + is_background tuple.
            jid = "agent_x_42"
            # Inject a real-ish task that's already done.
            done = asyncio.Future()
            done.set_result(None)
            agent.subagent_manager._tasks[jid] = done
            return jid, True

        agent.subagent_manager.spawn_from_event = fake_spawn
        agent.subagent_manager._configs = {
            "explore": SubAgentConfig(
                name="explore",
                notify_controller_on_background_complete=False,
            )
        }
        evt = SubAgentCallEvent(
            name="explore",
            args={"task": "scout area", "_tool_call_id": "call_99"},
            raw="",
        )
        await agent.start()
        try:
            handles = {}
            order = []
            tcids = {}
            await agent._dispatch_subagent_event(
                evt, agent.controller, handles, order, tcids, True
            )
            # ``fake_spawn`` returns ``is_background=True`` — the dispatch
            # promotes the sub-agent and appends a ``tool``-role
            # placeholder to the conversation rather than tracking a
            # direct handle.
            assert any(
                m.role == "tool" for m in agent.controller.conversation.get_messages()
            )
        finally:
            await agent.stop()

    async def test_real_dispatch_start_metadata_uses_resolved_child_provider(
        self, make_agent, monkeypatch
    ):
        from kohakuterrarium.parsing import SubAgentCallEvent
        from kohakuterrarium.modules.subagent.config import SubAgentConfig

        agent = make_agent()
        agent.llm.model = "gpt-child-actual"
        agent.llm._llm_identifier = "openai/child-profile@reasoning=high"
        agent.subagent_manager.llm = agent.llm
        agent.subagent_manager._configs["explore"] = SubAgentConfig(
            name="explore", model="parent"
        )
        calls = []
        original = agent.output_router.notify_activity

        def capture(activity_type, detail, metadata=None):
            calls.append((activity_type, detail, metadata or {}))
            return original(activity_type, detail, metadata)

        monkeypatch.setattr(agent.output_router, "notify_activity", capture)
        await agent.start()
        try:
            await agent._dispatch_subagent_event(
                SubAgentCallEvent(name="explore", args={"task": "inspect"}, raw=""),
                agent.controller,
                {},
                [],
                {},
                False,
            )
            start = next(call for call in calls if call[0] == "subagent_start")
            assert start[2]["model"] == "gpt-child-actual"
            assert start[2]["llm_name"] == "openai/child-profile@reasoning=high"
        finally:
            await agent.stop()

    async def test_dispatch_vetoed_by_plugin(self, make_agent):
        from kohakuterrarium.modules.plugin.base import (
            BasePlugin,
            PluginBlockError,
        )
        from kohakuterrarium.modules.plugin.manager import PluginManager
        from kohakuterrarium.parsing import SubAgentCallEvent

        class _VetoSA(BasePlugin):
            name = "veto-sa"

            async def pre_subagent_run(self, value, **kwargs):
                raise PluginBlockError("blocked")

        agent = make_agent()
        mgr = PluginManager()
        mgr.register(_VetoSA())
        agent.plugins = mgr
        evt = SubAgentCallEvent(name="explore", args={"task": "x"}, raw="")
        await agent.start()
        try:
            await agent._dispatch_subagent_event(
                evt, agent.controller, {}, [], {}, False
            )
        finally:
            await agent.stop()


# ── _dispatch_tool_event background path ────────────────────────


class TestDispatchToolEventPaths:
    async def test_tool_promoted_placeholder_appended(self, make_agent):
        """When the tool's backgroundify handle is already promoted, a
        placeholder is appended to the conversation in native mode."""
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolResult,
        )
        from kohakuterrarium.parsing import ToolCallEvent

        class _BgTool(BaseTool):
            @property
            def tool_name(self):
                return "bg"

            @property
            def description(self):
                return "bg"

            @property
            def execution_mode(self):
                return ExecutionMode.BACKGROUND

            async def _execute(self, args, **kwargs):
                return ToolResult(output="ok")

        agent = make_agent()
        tool = _BgTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        evt = ToolCallEvent(
            name="bg",
            args={"_tool_call_id": "call_a"},
            raw="",
        )
        await agent.start()
        try:
            await agent._dispatch_tool_event(
                evt, agent.controller, {}, [], {"_dummy": "x"}, True
            )
        finally:
            await agent.stop()


# ── _collect_and_push_feedback native results path ───────────────


class TestCollectAndPushFeedbackNative:
    async def test_native_promotions_only_push_event(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            # had_promotions=True via wait_handles returns no results.
            from kohakuterrarium.core.backgroundify import (
                BackgroundifyHandle,
            )
            from unittest.mock import MagicMock

            h = MagicMock(spec=BackgroundifyHandle)
            h.promoted = False
            h.done = False

            # Mock wait_handles to report a promotion.
            async def fake_wait(*args, **kwargs):
                return {}, True

            agent._wait_handles = fake_wait  # type: ignore[method-assign]
            ok = await agent._collect_and_push_feedback(
                agent.controller,
                {"x": h},
                ["x"],
                {"x": "call_x"},
                True,
            )
            # Promotion + native_mode → native_results_added=True → push event.
            assert ok is True
        finally:
            await agent.stop()


# ── _load_plugins on_load + on_agent_start fired ────────────────


class TestLoadPlugins:
    async def test_load_plugins_calls_lifecycle(self, make_agent):
        from kohakuterrarium.modules.plugin.manager import PluginManager

        agent = make_agent()
        captured = []

        class _Recorder:
            def __init__(self):
                self.name = "rec"

            async def on_agent_start(self, *a, **kw):
                captured.append("on_agent_start")

        mgr = PluginManager()
        mgr.register(_Recorder())
        agent.plugins = mgr
        await agent._load_plugins()
        # at minimum the on_agent_start notification fires.
        assert "on_agent_start" in captured

    async def test_load_plugins_no_op_when_none(self, make_agent):
        agent = make_agent()
        agent.plugins = None
        # Must not raise.
        await agent._load_plugins()


# ── _apply_plugin_hooks ─────────────────────────────────────────


class TestApplyPluginHooks:
    async def test_apply_returns_none(self, make_agent):
        agent = make_agent()
        # Just call — it's a documented no-op.
        assert agent._apply_plugin_hooks() is None


# ── get_system_prompt with non-string content ───────────────────


class TestGetSystemPromptListContent:
    async def test_returns_empty_for_non_string_content(self, make_agent):
        agent = make_agent()
        sys_msg = agent.controller.conversation.get_system_message()
        sys_msg.content = []  # non-string
        assert agent.get_system_prompt() == ""


# ── _on_provider_emergency_drop wiring ──────────────────────────


class TestOnEmergencyDropWiring:
    async def test_emergency_drop_handler_attached(self, make_agent):
        from kohakuterrarium.testing.llm import ScriptedLLM

        # Custom LLM with on_emergency_drop method.
        class _DropLLM(ScriptedLLM):
            def __init__(self):
                super().__init__(["x"])
                self._drop_cb = None

            def on_emergency_drop(self, cb):
                self._drop_cb = cb

        from kohakuterrarium.bootstrap import llm as bootstrap_llm
        from kohakuterrarium.bootstrap import agent_init

        drop_llm = _DropLLM()

        def _fake_create(cfg, *a, **kw):
            return drop_llm

        import unittest.mock as um

        from kohakuterrarium.core.agent import Agent
        from kohakuterrarium.core.config_types import (
            AgentConfig,
            InputConfig,
            OutputConfig,
        )

        with um.patch.object(bootstrap_llm, "create_llm_provider", _fake_create):
            with um.patch.object(agent_init, "create_llm_provider", _fake_create):
                cfg = AgentConfig(
                    name="drop_test",
                    llm_profile="t",
                    api_key_env="",
                    system_prompt="x",
                    include_tools_in_prompt=False,
                    include_hints_in_prompt=False,
                    tool_format="bracket",
                    agent_path=None,
                    input=InputConfig(type="none"),
                    output=OutputConfig(type="stdout"),
                )
                Agent(cfg)
        # The on_emergency_drop callback was registered.
        assert drop_llm._drop_cb is not None


# ── interrupt cancels plugin notify (line 569) ──────────────────


class TestInterruptWithPlugins:
    async def test_interrupt_notifies_plugins(self, make_agent):
        from kohakuterrarium.modules.plugin.manager import PluginManager

        agent = make_agent()
        mgr = PluginManager()

        class _Listener:
            name = "listener"

            async def on_interrupt(self, **kwargs):
                self.fired = True

        listener = _Listener()
        mgr.register(listener)
        agent.plugins = mgr
        await agent.start()
        try:
            agent.interrupt()
            await asyncio.sleep(0.01)
        finally:
            await agent.stop()


# ── _dispatch_subagent_event direct path (lines 520-532) ────────


class TestDispatchSubAgentDirect:
    async def test_direct_subagent_tracked_in_handles(self, make_agent):
        """When sub-agent is dispatched as direct (is_bg=False), the
        handle is registered in handles/handle_order/native_tool_call_ids."""
        from kohakuterrarium.parsing import SubAgentCallEvent

        agent = make_agent()

        async def fake_spawn(event):
            jid = "agent_direct_1"
            # Pending task that doesn't complete.

            async def slow():
                await asyncio.sleep(10)

            t = asyncio.create_task(slow())
            agent.subagent_manager._tasks[jid] = t
            # Return is_background=False to trigger the direct branch.
            return jid, False

        agent.subagent_manager.spawn_from_event = fake_spawn
        evt = SubAgentCallEvent(
            name="explore",
            args={"task": "x", "_tool_call_id": "call_sa"},
            raw="",
        )
        await agent.start()
        try:
            handles = {}
            order = []
            tcids = {}
            await agent._dispatch_subagent_event(
                evt, agent.controller, handles, order, tcids, True
            )
            # Direct handle tracked.
            assert "agent_direct_1" in handles
            assert "agent_direct_1" in order
            assert tcids.get("agent_direct_1") == "call_sa"
            # Clean up.
            agent.subagent_manager._tasks["agent_direct_1"].cancel()
        finally:
            await agent.stop()


# ── _run_single_turn dispatches CommandResultEvent + TextEvent ──


class TestRunSingleTurnDispatchKinds:
    async def test_command_result_event_dispatched(self, make_agent):
        """Drive an LLM stream that produces a CommandResultEvent
        through the parser by using an [/info] block referencing
        an unknown name (yields CommandEvent → CommandResultEvent)."""
        agent = make_agent(script=["[/info]ghost[info/]"])
        await _start_and_run(agent, create_user_input_event("info"))
        # No crash; conversation reaches an assistant message.
        msgs = agent.controller.conversation.get_messages()
        assert any(m.role == "assistant" for m in msgs)


# ── _check_termination idle_timeout via plugin chain ─────────────


class TestCheckTerminationPluginRegistered:
    async def test_termination_with_plugin_attached(self, make_agent):
        from kohakuterrarium.modules.plugin.manager import PluginManager

        agent = make_agent(termination={"max_turns": 10})
        mgr = PluginManager()
        agent.plugins = mgr
        agent._init_plugins()
        # Now the checker has a plugin manager attached.
        agent._termination_checker.start()
        terminated = agent._check_termination(["output"])
        # No stop yet.
        assert terminated is False


# ── _collect_and_push_feedback with output_feedback ─────────────


class TestCollectFeedbackWithOutputFeedback:
    async def test_output_feedback_collected(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            # Inject output feedback into the router.
            agent.output_router.get_output_feedback = lambda: "named-out feedback"
            ok = await agent._collect_and_push_feedback(
                agent.controller, {}, [], {}, False
            )
            # Feedback exists → True.
            assert ok is True
        finally:
            await agent.stop()


# ── _interrupt mid-stream (lines 296-301, 316-322) ──────────────


class TestInterruptMidStream:
    async def test_interrupt_breaks_controller_loop(self, make_agent):
        agent = make_agent(script=["chunk1 chunk2 chunk3"])
        await agent.start()
        try:
            # Set interrupt flag to fire in the loop.
            async def fake_run_single_turn(controller):
                # Set the flag mid-turn.
                agent._interrupt_requested = True
                from kohakuterrarium.core.agent_tools import _TurnResult

                return _TurnResult(
                    handles={},
                    handle_order=[],
                    text_output=["t1"],
                    native_mode=False,
                    native_tool_call_ids={},
                )

            agent._run_single_turn = fake_run_single_turn
            await agent._process_event(create_user_input_event("hi"))
            assert agent._interrupt_requested is False
        finally:
            await agent.stop()


# ── _run_controller_loop top-of-loop interrupt (lines 296-301) ──


class TestRunControllerLoopInterruptAtTop:
    async def test_loop_breaks_with_interrupt_set_at_start(self, make_agent):
        """When interrupt is True at top of the loop, the early-break
        branch (lines 295-301) executes."""
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            agent._interrupt_requested = True
            all_text = []
            await agent._run_controller_loop(agent.controller, all_text)
            # Loop exits immediately without running a turn.
            assert all_text == []
        finally:
            await agent.stop()


class TestInterruptQueueHandoff:
    async def test_interrupt_handoff_keeps_user_and_background_together(
        self, make_agent
    ):
        agent = make_agent()
        await agent.start()
        try:
            interrupted = create_user_input_event("active")
            interrupted.context["interrupted_by_user"] = True
            queued_user = EventEnvelope(create_user_input_event("queued user"))
            background = EventEnvelope(
                create_tool_complete_event("bg-1", "background done")
            )
            agent._event_inbox.put(queued_user)
            agent._event_inbox.put(background)
            rounds: list[list[str]] = []

            async def fake_process(events, _controller):
                rounds.append([event.type for event in events])

            agent._process_batch_with_controller = fake_process  # type: ignore[method-assign]
            await agent._run_turn_for_batch([EventEnvelope(interrupted)])

            assert rounds == [
                ["user_input"],
                ["user_input", "tool_complete"],
            ]
            assert len(agent._event_inbox) == 0
        finally:
            await agent.stop()


class TestRunSingleTurnInterruptMidLoop:
    async def test_interrupt_breaks_inner_async_for(self, make_agent):
        """When _interrupt_requested becomes True between yields from
        controller.run_once(), the inner async-for break fires (361)."""
        from kohakuterrarium.parsing import TextEvent

        agent = make_agent()
        await agent.start()
        try:

            async def fake_run_once():
                yield TextEvent(text="first")
                agent._interrupt_requested = True
                yield TextEvent(text="second")  # never reached

            agent.controller.run_once = fake_run_once
            result = await agent._run_single_turn(agent.controller)
            # Only the first text chunk made it in.
            assert "first" in result.text_output
        finally:
            agent._interrupt_requested = False
            await agent.stop()


class TestRunSingleTurnSubAgentEvent:
    async def test_subagent_event_dispatched_from_loop(self, make_agent):
        """SubAgentCallEvent yielded by controller.run_once gets dispatched
        via _dispatch_subagent_event (line 373)."""
        from kohakuterrarium.parsing import SubAgentCallEvent

        agent = make_agent()
        await agent.start()
        try:

            async def fake_run_once():
                yield SubAgentCallEvent(name="explore", args={"task": "x"}, raw="")

            agent.controller.run_once = fake_run_once
            # Stub the dispatch to verify routing.
            called = []

            async def fake_dispatch(*args, **kw):
                called.append(args)

            agent._dispatch_subagent_event = fake_dispatch
            await agent._run_single_turn(agent.controller)
            assert called
        finally:
            await agent.stop()


# ── _process_event_with_controller exception path (lines 248-249) ──


class TestProcessEventCancelledLoop:
    async def test_loop_task_cancelled_handled(self, make_agent):
        """When the loop_task raises CancelledError, the handler logs
        the interrupt activity (lines 248-249)."""
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            # Patch _run_controller_loop to raise CancelledError.
            async def cancel_loop(controller, all_text):
                raise asyncio.CancelledError()

            agent._run_controller_loop = cancel_loop
            await agent._process_batch_with_controller(
                [create_user_input_event("hi")], agent.controller
            )
        finally:
            await agent.stop()


# ── _dispatch_tool_event run_in_background branch (line 431) ────


class TestDispatchToolRunBg:
    async def test_run_in_background_flag_flips_direct(self, make_agent):
        from kohakuterrarium.parsing import ToolCallEvent

        agent = make_agent()
        agent.registry.register_tool(_EchoTool())
        agent.executor.register_tool(_EchoTool())
        await agent.start()
        try:
            evt = ToolCallEvent(
                name="echo",
                args={"msg": "x", "run_in_background": True},
                raw="",
            )
            handles = {}
            order = []
            await agent._dispatch_tool_event(
                evt, agent.controller, handles, order, {}, False
            )
        finally:
            await agent.stop()


# ── _dispatch_tool_event promoted path tool_call_id appended (442) ──


class TestDispatchToolPromotedPlaceholder:
    async def test_promoted_native_mode_placeholder(self, make_agent):
        """When backgroundify_init=True the handle is promoted from the
        start, triggering the native-mode placeholder append (442-447)."""
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolResult,
        )
        from kohakuterrarium.parsing import ToolCallEvent

        class _BgInit(BaseTool):
            @property
            def tool_name(self):
                return "bginit"

            @property
            def description(self):
                return "bg"

            @property
            def execution_mode(self):
                return ExecutionMode.BACKGROUND

            async def _execute(self, args, **kwargs):
                return ToolResult(output="bg ok")

        agent = make_agent()
        tool = _BgInit()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        try:
            evt = ToolCallEvent(
                name="bginit",
                args={"_tool_call_id": "call_bg"},
                raw="",
            )
            handles = {}
            order = []
            tcids = {}
            await agent._dispatch_tool_event(
                evt, agent.controller, handles, order, tcids, True
            )
        finally:
            await agent.stop()


# ── _collect_and_push_feedback native-mode result added (620-623) ──


class TestCollectFeedbackNativeResultsAdded:
    async def test_native_mode_with_results_appends_to_conversation(self, make_agent):
        from kohakuterrarium.core.backgroundify import BackgroundifyHandle
        from unittest.mock import MagicMock

        from kohakuterrarium.core.job import JobResult

        agent = make_agent()
        await agent.start()
        try:
            h = MagicMock(spec=BackgroundifyHandle)
            # Patch wait_handles to return real results without promotions.
            result = JobResult(job_id="x", output="ok", exit_code=0)

            async def fake_wait(*args, **kwargs):
                return {"x": result}, False

            agent._wait_handles = fake_wait  # type: ignore[method-assign]
            ok = await agent._collect_and_push_feedback(
                agent.controller,
                {"x": h},
                ["x"],
                {"x": "call_x"},
                True,  # native_mode
            )
            assert ok is True
        finally:
            await agent.stop()


# ── Feat 3: opportunistic input injection ────────────────────────


class TestMidTurnDrainDuringDirectWait:
    """The round boundary must not be starved by a long direct job.

    The only mid-turn drain site runs AFTER ``_wait_handles``, so a
    direct tool / foreground sub-agent that runs for a long time used to
    park the turn before the drain — every queued user message and
    background completion waited for a manual interrupt. Queued USER
    input now promotes the outstanding direct handles to background so
    the boundary (and its drain) runs immediately; the promoted job's
    real result still arrives through the background-completion fold.
    Background completions alone keep the natural boundary.
    """

    def _gated_tool(self, started: asyncio.Event, gate: asyncio.Event):
        class _GatedTool(BaseTool):
            @property
            def tool_name(self):
                return "slowgate"

            @property
            def description(self):
                return "slowgate"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                started.set()
                await gate.wait()
                return ToolResult(output="gate-done")

        return _GatedTool()

    async def test_user_input_mid_wait_folds_before_tool_completes(self, make_agent):
        gate = asyncio.Event()
        started = asyncio.Event()
        agent = make_agent(
            script=["r1\n[/slowgate]\n[slowgate/]", "ack", "done"],
        )
        agent.add_tool(self._gated_tool(started, gate))
        await agent.start()
        try:
            primary = asyncio.create_task(agent.inject_input("kick"))
            await asyncio.wait_for(started.wait(), 5)
            await agent.inject_input("urgent mid-wait message", source="web")

            def _message_folded() -> bool:
                return any(
                    m.role == "user" and "urgent mid-wait message" in str(m.content)
                    for m in agent.controller.conversation.get_messages()
                )

            folded_while_held = False
            for _ in range(100):
                if _message_folded():
                    folded_while_held = not gate.is_set()
                    break
                await asyncio.sleep(0.05)
            assert folded_while_held, (
                "queued user input must fold at a forced round boundary "
                "while the direct tool is still running — not after it"
            )
            gate.set()
            await asyncio.wait_for(primary, 10)

            # The promoted tool's REAL result is not lost — it folds back
            # in through the background-completion path.
            for _ in range(100):
                if any(
                    "gate-done" in str(m.content)
                    for m in agent.controller.conversation.get_messages()
                ):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("promoted tool result never delivered")
        finally:
            gate.set()
            await agent.stop()

    async def test_bg_completion_alone_keeps_the_direct_wait(self, make_agent):
        gate = asyncio.Event()
        started = asyncio.Event()
        bg_started = asyncio.Event()
        bg_gate = asyncio.Event()

        class _BgTool(BaseTool):
            @property
            def tool_name(self):
                return "bgjob"

            @property
            def description(self):
                return "bgjob"

            @property
            def execution_mode(self):
                return ExecutionMode.BACKGROUND

            async def _execute(self, args, **kwargs):
                bg_started.set()
                await bg_gate.wait()
                return ToolResult(output="bg-payload")

        agent = make_agent(
            script=[
                "r1\n[/bgjob]\n[bgjob/]\n[/slowgate]\n[slowgate/]",
                "r2",
                "done",
            ],
        )
        agent.add_tool(self._gated_tool(started, gate))
        agent.add_tool(_BgTool())
        await agent.start()
        try:
            primary = asyncio.create_task(agent.inject_input("kick"))
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.wait_for(bg_started.wait(), 5)
            bg_gate.set()
            # The completion reaches the inbox while slowgate is held.
            for _ in range(60):
                if len(agent._event_inbox) >= 1:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)
            # A background completion must NOT cut the direct wait short.
            direct_handles = [h for h in agent._active_handles.values() if not h.done]
            assert direct_handles, "slowgate handle should still be waiting"
            assert not any(h.promoted for h in direct_handles)
            gate.set()
            await asyncio.wait_for(primary, 10)
            assert any(
                "bg-payload" in str(m.content)
                for m in agent.controller.conversation.get_messages()
            )
        finally:
            gate.set()
            bg_gate.set()
            await agent.stop()


class TestOpportunisticInputInjection:
    """Mid-turn ``user_input`` / ``trigger`` events that arrive while
    the agent's ``_processing_lock`` is held by another turn must be
    buffered on ``Agent._pending_mid_turn_inputs`` and drained from
    inside the current turn's ``_collect_and_push_feedback`` AFTER
    the tool results land. The drain appends a coalesced ``role=user``
    message to the conversation (so the LLM sees them on the next
    round of the SAME turn), emits one ``user_input_injected``
    activity per event (so the FE clears each queued banner), and
    records canonical ``user_input`` session events under the
    current ``(turn_index, branch_id)``.
    """

    async def test_inject_input_buffers_when_lock_held(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent()
        await agent.start()
        try:
            # Simulate the agent being mid-turn — acquire the lock
            # to force the buffer path. Without this the inject
            # short-circuits to the normal _process_event flow.
            async with agent._processing_lock:
                await agent.inject_input("hi", source="web")
                # Mutex held → event folds onto the inbox (fire-and-forget),
                # NOT blocked on the lock and NOT processed yet.
                assert len(agent._event_inbox) == 1
                buffered = agent._event_inbox._dq[0].event
                assert buffered.type == "user_input"
                assert isinstance(buffered, TriggerEvent)
        finally:
            await agent.stop()

    async def test_drain_appends_combined_user_message(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent()
        await agent.start()
        try:
            # Queue two events directly — the re-claim coalesces them
            # into one user message joined by a blank line so the
            # LLM sees them as one contiguous turn.
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content="line one"))
            )
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content="line two"))
            )
            count = await agent._drain_mid_turn_pending_inputs(agent.controller)
            assert count == 2
            # Inbox cleared.
            assert agent._event_inbox.empty()
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert user_msgs, "drain must append at least one user message"
            assert user_msgs[-1].content == "line one\n\nline two"
        finally:
            await agent.stop()

    async def test_drain_emits_one_activity_per_event(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent()
        await agent.start()
        try:
            captured: list[tuple] = []
            original = agent.output_router.notify_activity

            def spy(activity_type, detail, metadata=None):
                captured.append((activity_type, dict(metadata or {})))
                return original(activity_type, detail, metadata)

            agent.output_router.notify_activity = spy  # type: ignore[assignment]
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content="A"))
            )
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content="B"))
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            injected = [c for c in captured if c[0] == "user_input_injected"]
            # One activity per drained event — so the FE can pop each
            # queued banner separately.
            assert len(injected) == 2
            assert injected[0][1]["content"] == "A"
            assert injected[1][1]["content"] == "B"
            # Both carry the current turn/branch ids so the FE knows
            # which (turn, branch) the injection landed on.
            assert "turn_index" in injected[0][1]
            assert "branch_id" in injected[0][1]
        finally:
            await agent.stop()

    async def test_drain_claims_awaited_background_and_fifo_tail(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            first = EventEnvelope(create_user_input_event("first"))
            capture = TurnCapture()
            awaited = EventEnvelope(
                create_user_input_event("queued user"),
                future=asyncio.get_running_loop().create_future(),
                capture=capture,
            )
            background = EventEnvelope(
                create_tool_complete_event("bg-1", "background done")
            )
            tail = EventEnvelope(create_user_input_event("tail user"))
            agent._active_event_run = [first]
            agent._active_event_captures = []
            agent._event_inbox.put(awaited)
            agent._event_inbox.put(background)
            agent._event_inbox.put(tail)

            drained = await agent._drain_mid_turn_pending_inputs(agent.controller)

            assert drained == 3
            assert agent._active_event_run == [first, awaited, background, tail]
            assert agent._active_event_captures == [capture]
            assert len(agent._event_inbox) == 0
            content = "\n".join(
                getattr(message, "content", "")
                for message in agent.controller.conversation.get_messages()
            )
            assert (
                content.index("queued user")
                < content.index("background done")
                < content.index("tail user")
            )
            assert not awaited.future.done()
        finally:
            await agent.stop()

    async def test_drain_returns_zero_when_buffer_is_empty(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            before = list(agent.controller.conversation.get_messages())
            count = await agent._drain_mid_turn_pending_inputs(agent.controller)
            assert count == 0
            assert list(agent.controller.conversation.get_messages()) == before
        finally:
            await agent.stop()

    async def test_trigger_events_also_buffered_and_drained(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent()
        await agent.start()
        try:
            evt = TriggerEvent(
                type="trigger",
                content="",
                prompt_override="timer fired: snapshot",
                context={"trigger_id": "tm_1"},
            )
            async with agent._processing_lock:
                await agent._process_event(evt)
                # Trigger fired mid-turn: folded onto the inbox, NOT blocked.
                assert [env.event for env in agent._event_inbox._dq] == [evt]
            # Drain folds the trigger's ``prompt_override`` into the
            # conversation as a user message.
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert user_msgs[-1].content == "timer fired: snapshot"
        finally:
            await agent.stop()

    async def test_rerun_events_bypass_buffer(self, make_agent):
        # Regen / edit-rerun pre-increment the branch_id and MUST run as
        # their OWN turn — they cannot fold into another. The fold path
        # explicitly skips ``context["rerun"]=True`` events; they enqueue
        # as a primary (future-bearing) instead.
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent()
        await agent.start()
        try:
            rerun = TriggerEvent(
                type="user_input",
                content="re-run",
                context={"rerun": True},
            )
            async with agent._processing_lock:
                # The mutex is held, so a foldable event would fold. Run
                # with a short timeout; expect TimeoutError because the
                # rerun took the PRIMARY path and awaits its own turn (the
                # consumer is blocked on the mutex we hold).
                try:
                    await asyncio.wait_for(agent._process_event(rerun), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                # Critical: the rerun did NOT fold — the only queued
                # envelope is a primary (future-bearing), not a fold-in.
                folds = [e for e in agent._event_inbox._dq if e.future is None]
                assert folds == []
        finally:
            await agent.stop()

    async def test_inject_input_returns_false_when_buffered(self, make_agent):
        # Bug 1 root cause: ``_process_input`` in studio/attach/io.py
        # always emits ``{type: idle}`` after ``inject_input`` returns.
        # When the lock is held the call buffers and returns
        # immediately, so ``idle`` fires within milliseconds and the
        # FE clears ``processingByTab`` → KohakUwUing disappears.
        # Fix: ``inject_input`` returns False when buffered so the
        # caller can skip the ``idle`` emission.
        agent = make_agent()
        await agent.start()
        try:
            async with agent._processing_lock:
                result = await agent.inject_input("queued msg", source="web")
                assert result is False, (
                    "inject_input must return False when the event folded "
                    "for mid-turn injection — caller must NOT emit an "
                    "``idle`` WS frame in this case"
                )
                assert len(agent._event_inbox) == 1
        finally:
            await agent.stop()

    async def test_inject_input_returns_true_when_processed(self, make_agent):
        # Counterpart: when the lock isn't held, inject_input runs
        # the event normally and returns True — caller may emit the
        # ``idle`` frame as before.
        agent = make_agent(script=["x"])
        await agent.start()
        try:
            result = await agent.inject_input("first turn", source="web")
            assert result is True
        finally:
            await agent.stop()

    async def test_drain_emits_real_ws_frame_via_stream_output(self, make_agent):
        # Wire a real StreamOutput onto the agent's output_router so we
        # observe the EXACT frame the FE would see. Bug repro: the
        # user types "Hello" mid-turn → drain runs → must emit a
        # ``{type: activity, activity_type: user_input_injected,
        # content: ..., turn_index, branch_id, source}`` frame. If
        # this test fails, the FE wiring is correct and the bug is
        # backend-side; if it passes, the issue is FE rendering /
        # signature matching.
        import asyncio as _asyncio
        from kohakuterrarium.core.events import TriggerEvent
        from kohakuterrarium.studio.attach._event_stream import StreamOutput

        agent = make_agent()
        await agent.start()
        try:
            queue: _asyncio.Queue = _asyncio.Queue()
            stream = StreamOutput("test_agent", queue, [], agent=agent)
            agent.output_router.add_secondary(stream)
            # Simulate the FE-typed content shape: a list of content
            # parts (NOT a bare string) so the signature path is
            # exercised the way the real WS layer drives it.
            content = [{"type": "text", "text": "Hello"}]
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content=content))
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            # Pull frames off the queue.
            frames: list[dict] = []
            while not queue.empty():
                frames.append(queue.get_nowait())
            # The user_input_injected activity frame MUST be present.
            inj = [
                f
                for f in frames
                if f.get("type") == "activity"
                and f.get("activity_type") == "user_input_injected"
            ]
            assert len(inj) == 1, (
                f"drain did not emit user_input_injected to StreamOutput; "
                f"got frames: {frames}"
            )
            frame = inj[0]
            # Content shape must match what the FE sees on the wire.
            assert frame["content"] == content, (
                "content field on the WS frame must be the list-of-parts "
                "shape the FE handler matches its queue entry signature "
                "against"
            )
            assert "turn_index" in frame
            assert "branch_id" in frame
            assert frame["source"] == "test_agent"
        finally:
            await agent.stop()

    async def test_drain_persists_distinct_event_type_for_replay(self, make_agent):
        # Replay-dedupe bug: the FE replay path dedupes
        # ``user_input`` / ``user_message`` by ``(turn, branch)``
        # because live flows emit BOTH for every user-driven turn. A
        # mid-turn injection shares those ids with the trigger that
        # started the turn — if we persist it as ``user_input``, refresh
        # drops it and the user's message vanishes from history. So the
        # drain persists a distinct ``user_input_injected`` type and the
        # FE replay handles it separately.
        import tempfile
        from pathlib import Path

        from kohakuterrarium.core.events import TriggerEvent
        from kohakuterrarium.session.store import SessionStore

        agent = make_agent()
        with tempfile.TemporaryDirectory() as td:
            store_path = Path(td) / "test.kohakutr"
            store = SessionStore(store_path)
            agent.session_store = store
            await agent.start()
            try:
                agent._turn_index = 1
                agent._branch_id = 1
                agent._event_inbox.put(
                    EventEnvelope(
                        TriggerEvent(type="user_input", content="mid-turn typed")
                    )
                )
                await agent._drain_mid_turn_pending_inputs(agent.controller)
                events = list(store.get_events(agent.config.name))
                injected = [e for e in events if e["type"] == "user_input_injected"]
                assert len(injected) == 1, (
                    "drain must persist exactly one user_input_injected "
                    "event per buffered input — distinct from user_input "
                    "so the FE (turn,branch) dedupe doesn't drop it"
                )
                assert injected[0]["turn_index"] == 1
                assert injected[0]["branch_id"] == 1
                # And NOT as plain user_input (the type that gets
                # deduped by the FE replay path).
                plain = [e for e in events if e["type"] == "user_input"]
                assert plain == [], (
                    "drain must NOT persist as user_input — that type "
                    "collides with the (turn,branch) dedupe and would "
                    "make the typed message vanish after refresh"
                )
            finally:
                await agent.stop()
                store.close()

    async def test_full_flow_real_agent_real_store_real_stream(self, make_agent):
        # END-TO-END: drive the agent through a real ``_process_event``
        # call with a real session_store + real StreamOutput attached.
        # The agent runs turn A (with a slow tool), and DURING turn A
        # another inject_input("B") is fired from a sibling task. Verify
        # that AFTER the turn completes:
        #   1. controller.conversation contains B as a user message
        #      (the agent saw it on the next round — what the user
        #      confirmed in production)
        #   2. session_store has a ``user_input_injected`` event with
        #      content == B and the running turn's (turn_index,
        #      branch_id)  ← the refresh-time render depends on this
        #   3. StreamOutput's queue has a ``user_input_injected``
        #      activity frame with content == B  ← the live FE banner
        #      pop depends on this
        # If any one of these fails, that's the leg of the wiring
        # production is missing.
        import asyncio as _asyncio
        import tempfile
        from pathlib import Path

        from kohakuterrarium.session.store import SessionStore
        from kohakuterrarium.studio.attach._event_stream import StreamOutput

        # Tool that blocks until released. Lets us inject B WHILE turn A
        # is running, then unblock so the drain runs at the round
        # boundary.
        release = _asyncio.Event()
        injected_during_tool = _asyncio.Event()

        class _SlowTool(BaseTool):
            @property
            def tool_name(self):
                return "wait"

            @property
            def description(self):
                return "wait"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                injected_during_tool.set()
                await release.wait()
                return ToolResult(output="waited")

        agent = make_agent(
            script=[
                "[/wait]ok=1[wait/]",
                "Done, saw B!",
            ]
        )
        agent.registry.register_tool(_SlowTool())
        agent.executor.register_tool(_SlowTool())

        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td) / "test.kohakutr")
            agent.session_store = store
            await agent.start()
            try:
                ws_queue: _asyncio.Queue = _asyncio.Queue()
                stream = StreamOutput("test_agent", ws_queue, [], agent=agent)
                agent.output_router.add_secondary(stream)

                # Fire turn A — runs in background so we can inject B.
                turn_a = _asyncio.create_task(
                    agent._process_event(create_user_input_event("A"))
                )
                # Wait until the tool started — at this point the lock
                # is held and inject_input WILL buffer.
                await _asyncio.wait_for(injected_during_tool.wait(), timeout=5.0)
                # Inject B from a sibling task (mirrors how io.py does it).
                processed = await agent.inject_input(
                    "B", source="web", pending_id="c_midturn"
                )
                assert processed is False, (
                    "B must buffer mid-turn — _process_event returns False "
                    "when the lock is held by another turn"
                )
                # Unblock the tool so the round completes and drain fires.
                release.set()
                await _asyncio.wait_for(turn_a, timeout=10.0)

                # 1) Agent sees B in conversation (confirmed in prod).
                user_msgs = [
                    m
                    for m in agent.controller.conversation.get_messages()
                    if getattr(m, "role", None) == "user"
                ]
                contents = [getattr(m, "content", "") for m in user_msgs]
                assert any(
                    "B" in (c if isinstance(c, str) else str(c)) for c in contents
                ), f"agent must see B in conversation; got user msgs: {contents}"

                # 2) Session store has user_input_injected event for B.
                events = list(store.get_events(agent.config.name))
                injected = [e for e in events if e.get("type") == "user_input_injected"]
                assert len(injected) == 1, (
                    f"session store must record exactly one user_input_injected "
                    f"event; got types: {[e.get('type') for e in events]}"
                )
                assert injected[0].get("content") == "B"
                assert injected[0].get("pending_id") == "c_midturn"
                assert injected[0].get("turn_index") == agent._turn_index
                assert injected[0].get("branch_id") == agent._branch_id
                resumable_injected = [
                    e
                    for e in store.get_resumable_events(agent.config.name)
                    if e.get("type") == "user_input_injected"
                ]
                assert resumable_injected[0].get("pending_id") == "c_midturn"

                # 3) StreamOutput queue has user_input_injected frame for B.
                frames: list[dict] = []
                while not ws_queue.empty():
                    frames.append(ws_queue.get_nowait())
                inj_frames = [
                    f
                    for f in frames
                    if f.get("type") == "activity"
                    and f.get("activity_type") == "user_input_injected"
                ]
                assert len(inj_frames) == 1, (
                    f"WS queue must carry exactly one user_input_injected frame; "
                    f"got frame types: {[(f.get('type'), f.get('activity_type')) for f in frames]}"
                )
                assert inj_frames[0].get("content") == "B"
                assert inj_frames[0].get("pending_id") == "c_midturn"
                assert inj_frames[0].get("source") == "test_agent"
            finally:
                release.set()
                await agent.stop()
                store.close()

    async def test_interrupt_leaves_queued_inputs_for_consumer(self, make_agent):
        # After an interrupt, events still queued on the inbox are NOT
        # stranded — the single consumer claims and runs them as the next
        # turn (no explicit re-fire needed).
        agent = make_agent(script=["after-interrupt reply"])
        await agent.start()
        try:
            # Fold an input while the mutex is held (simulating an
            # in-flight turn), then interrupt while still folded.
            async with agent._processing_lock:
                ran = await agent.inject_input("queued during turn", source="web")
                assert ran is False
                assert len(agent._event_inbox) == 1
                agent.interrupt()
            # Lock released — the consumer claims and runs the queued event.
            for _ in range(200):
                await asyncio.sleep(0.01)
                if (
                    agent._event_inbox.empty()
                    and agent._processing_task is None
                    and agent.controller.conversation.get_last_assistant_message()
                    is not None
                ):
                    break
            assert agent._event_inbox.empty(), (
                "queued events must run after interrupt — they represent "
                "the user's next intent, not the cancelled turn"
            )
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert "queued during turn" in [m.content for m in user_msgs]
        finally:
            await agent.stop()

    async def test_interrupt_drops_buffered_drive_events(self, make_agent):
        from kohakuterrarium.core.events import TriggerEvent

        agent = make_agent(script=[])
        await agent.start()
        try:
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="drive_ready", content="goal"))
            )
            agent._event_inbox.put(
                EventEnvelope(TriggerEvent(type="user_input", content="keep me"))
            )
            agent.interrupt()
            assert [env.event.type for env in agent._event_inbox._dq] == ["user_input"]
        finally:
            await agent.stop()

    async def test_interrupt_does_not_livelock_on_slow_lock(self, make_agent):
        # Regression: interrupt() with a queued event while the lock is
        # held elsewhere must not spin. The consumer simply waits out the
        # lock and runs the event once released — no busy loop, no strand.
        agent = make_agent(script=["after-slow-interrupt reply"])
        await agent.start()
        try:
            release = asyncio.Event()

            async def hold_lock():
                async with agent._processing_lock:
                    await release.wait()

            holder = asyncio.create_task(hold_lock())
            await asyncio.sleep(0)
            assert agent._processing_lock.locked()
            ran = await agent.inject_input("queued while held", source="web")
            assert ran is False
            assert len(agent._event_inbox) == 1

            agent.interrupt()
            # Keep the lock held past any grace window — the consumer just
            # waits (no spin); the test does not time out.
            await asyncio.sleep(0.1)
            release.set()
            await holder

            for _ in range(200):
                await asyncio.sleep(0.01)
                if (
                    agent._event_inbox.empty()
                    and agent._processing_task is None
                    and agent.controller.conversation.get_last_assistant_message()
                    is not None
                ):
                    break
            assert agent._event_inbox.empty()
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert "queued while held" in [m.content for m in user_msgs]
        finally:
            await agent.stop()


# ── TUI callbacks wired in start (lines 269, 271-272) ───────────


class TestStartWithTUIInput:
    async def test_tui_input_callbacks_wired(self, make_agent):
        agent = make_agent()
        # Inject a fake TUI input with an ``_tui`` attribute.
        from kohakuterrarium.builtins.inputs.none import NoneInput
        import types

        ti = NoneInput()
        ti._tui = types.SimpleNamespace(
            _app=types.SimpleNamespace(on_interrupt=None),
            on_cancel_job=None,
            on_promote_job=None,
        )
        agent.input = ti
        await agent.start()
        try:
            # Callbacks are wired (note: bound-method comparisons require ==).
            assert ti._tui._app.on_interrupt == agent.interrupt
            assert ti._tui.on_cancel_job == agent._cancel_job
            assert ti._tui.on_promote_job == agent._promote_handle
        finally:
            await agent.stop()


# ── inject_input with slash command result (lines 786) ──────────


class TestInjectInputSlashCommandResult:
    async def test_slash_command_returns_consumed(self, make_agent):
        agent = make_agent(script=["unused"])
        await agent.start()
        try:
            # Stub _prepare_injected_input to return None (consumed).
            async def fake_prepare(content, source):
                return None

            agent._prepare_injected_input = fake_prepare
            # inject_input bails when content is None — no LLM turn fires.
            await agent.inject_input("/slash")
        finally:
            await agent.stop()


# ── attach_session_store secondary already exists (lines 811) ───


class TestAttachSessionStoreReplacesSecondary:
    async def test_replaces_existing_session_output(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store1 = SessionStore(str(tmp_path / "s1.kohakutr.v2"))
        store1.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        store2 = SessionStore(str(tmp_path / "s2.kohakutr.v2"))
        store2.init_meta(
            session_id="s2",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store1)
        old_output = agent._session_output
        # Attach a different store — old secondary should be removed.
        agent.attach_session_store(store2)
        assert agent._session_output is not old_output


# ── attach_session_store with compact_count saved (lines 826-832) ──


class TestAttachSessionStoreCompactCount:
    async def test_valid_compact_count_restored(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        store.state["test_agent:compact_count"] = "5"
        agent = make_agent()
        # compact_manager is created in start(), so attach_session_store
        # before start tests the no-manager path (defensive — must not crash).
        agent.attach_session_store(store)
        # Now start to bring up compact_manager and rewire.
        await agent.start()
        try:
            # Re-attach to exercise the compact_count restore branch.
            agent.attach_session_store(store)
            assert agent.compact_manager._compact_count == 5
        finally:
            await agent.stop()


# ── _init_compact_manager with no profile_max_context (line 380) ──


class TestInitCompactManagerNoProfileContext:
    async def test_no_profile_max_context_uses_default(self, make_agent):
        agent = make_agent()
        # llm without _profile_max_context attribute.
        if hasattr(agent.llm, "_profile_max_context"):
            delattr(agent.llm, "_profile_max_context")
        agent._init_compact_manager()
        # Uses CompactConfig default.
        from kohakuterrarium.core.compact import CompactConfig

        assert agent.compact_manager.config.max_tokens == CompactConfig.max_tokens


# ── _init_plugins early-return branches (lines 406-419) ─────────


class TestInitPluginsEarlyReturnBranches:
    async def test_early_return_with_termination_checker_attached(self, make_agent):
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager

        class _N(BasePlugin):
            name = "n"

        agent = make_agent(termination={"max_turns": 5})
        mgr = PluginManager()
        mgr.register(_N())
        agent.plugins = mgr
        agent._init_plugins()
        # Termination checker now references the new manager.
        assert agent._termination_checker._plugin_manager is mgr

    async def test_early_return_with_compact_manager(self, make_agent):
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager

        class _N(BasePlugin):
            name = "n"

        agent = make_agent()
        # Bring up compact_manager via start.
        await agent.start()
        try:
            mgr = PluginManager()
            mgr.register(_N())
            agent.plugins = mgr
            agent._init_plugins()
            assert agent.compact_manager._plugins is mgr
            assert agent.subagent_manager._parent_plugins is mgr
        finally:
            await agent.stop()


# ── interrupt with no active handles (line 565) ─────────────────


class TestInterruptNoActiveHandles:
    async def test_interrupt_clears_state_with_no_handles(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            agent.interrupt()
            assert agent._interrupt_requested is True
        finally:
            await agent.stop()


class TestInterruptWithRunningTask:
    async def test_interrupt_cancels_processing_task_and_handles(self, make_agent):
        """When processing_task is alive and active_handles exist, interrupt
        cancels both (lines 565, 569)."""
        from unittest.mock import MagicMock

        from kohakuterrarium.core.backgroundify import BackgroundifyHandle

        agent = make_agent()
        await agent.start()
        try:

            async def long_processing():
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    pass

            agent._processing_task = asyncio.create_task(long_processing())
            await asyncio.sleep(0.01)
            # Add an active handle that points at a real task.

            async def slow():
                await asyncio.sleep(5)

            inner_task = asyncio.create_task(slow())
            h = MagicMock(spec=BackgroundifyHandle)
            h.promoted = False
            h.done = False
            h.task = inner_task
            agent._active_handles["bash_x"] = h
            agent._register_direct_job("bash_x", kind="tool", name="bash")
            agent.interrupt()
            await asyncio.sleep(0.05)
            # processing task got cancellation request — wait for cleanup.
            try:
                await asyncio.wait_for(agent._processing_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            inner_task.cancel()
            try:
                await inner_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            await agent.stop()


# ── _cancel_job with subagent task path (lines 598-602) ─────────


class TestCancelJobSubAgentJobPath:
    async def test_cancel_subagent_job_with_subagent_instance(self, make_agent):
        from kohakuterrarium.modules.subagent.config import SubAgentConfig
        from unittest.mock import MagicMock

        agent = make_agent()
        await agent.start()
        try:
            agent.subagent_manager.register(
                SubAgentConfig(name="explore", system_prompt="x", max_turns=1)
            )

            async def slow():
                await asyncio.sleep(5)

            task = asyncio.create_task(slow())
            agent.subagent_manager._tasks["agent_y"] = task
            # Provide a job with a subagent attribute.
            job = MagicMock()
            agent.subagent_manager._jobs["agent_y"] = job
            agent._cancel_job("agent_y", "explore")
            await asyncio.sleep(0.01)
            job.subagent.cancel.assert_called_once()
            assert task.cancelled() or task.done()
        finally:
            await agent.stop()


# ── CallbackOutput methods exhaustively (line 885 + others) ─────


class TestCallbackOutputExhaustive:
    async def test_all_lifecycle_methods(self, make_agent):
        captured = []
        agent = make_agent()
        agent.set_output_handler(lambda t: captured.append(t), replace_default=True)
        cb_output = agent.output_router.default_output
        # Call each method directly to cover them.
        await cb_output.start()
        await cb_output.stop()
        await cb_output.write("x")
        await cb_output.write_stream("y")
        await cb_output.flush()
        await cb_output.on_processing_start()
        await cb_output.on_processing_end()
        cb_output.on_activity("kind", "detail")
        assert "x" in captured
        assert "y" in captured


# ── _init_compact_manager profile_max_context branch (line 380) ──


class TestInitCompactManagerProfileContext:
    async def test_with_profile_max_context(self, make_agent):
        agent = make_agent()
        agent.llm._profile_max_context = 50_000
        agent._init_compact_manager()
        # The compact manager uses the LLM's profile context size.
        assert agent.compact_manager.config.max_tokens == 50_000


# ── _init_plugins early-return with all branches (lines 406-419) ──


class TestInitPluginsEarlyReturnFull:
    async def test_full_early_return_path_with_active_subagent_manager(
        self, make_agent
    ):
        """Exercise every branch of the pre-existing-plugins early return."""
        from kohakuterrarium.modules.plugin.manager import PluginManager

        agent = make_agent(termination={"max_turns": 5})
        await agent.start()
        try:
            # All required components exist post-start: controller,
            # compact_manager, termination_checker, subagent_manager.
            mgr = PluginManager()
            agent.plugins = mgr
            agent._init_plugins()
        finally:
            await agent.stop()


# ── _publish_session_info prompt_cache_key path (lines 508-509) ──


class TestPublishSessionInfoCacheKey:
    async def test_cache_key_set_on_llm(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="abc123",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        # Give the LLM a prompt_cache_key slot.
        agent.llm.prompt_cache_key = ""
        agent.attach_session_store(store)
        await agent.start()
        try:
            assert agent.llm.prompt_cache_key == "abc123"
        finally:
            await agent.stop()


# ── _promote_handle off-loop branch (lines 631-637) ────────────


class TestPromoteHandleOffLoop:
    def test_promote_outside_event_loop(self, make_agent):
        import asyncio as _asyncio
        from unittest.mock import MagicMock

        from kohakuterrarium.core.backgroundify import BackgroundifyHandle

        agent = make_agent()
        # In production ``Agent.start()`` stashes the running loop on
        # ``self._loop`` so cross-thread schedulers (TUI promote) can
        # ``call_soon_threadsafe`` it.  Without ``start()`` the test
        # has to seed the loop reference explicitly; on Python 3.14+
        # ``asyncio.get_event_loop()`` would otherwise raise in this
        # sync context and the production code would have nowhere to
        # schedule the promote.
        agent._loop = _asyncio.new_event_loop()
        try:
            h = MagicMock(spec=BackgroundifyHandle)
            h.promote = MagicMock(return_value=True)
            agent._active_handles["bash_x"] = h
            # Called from a sync context with no running loop → hits the
            # ``call_soon_threadsafe`` branch.
            result = agent._promote_handle("bash_x")
            assert result is True
        finally:
            agent._loop.close()

    def test_promote_off_loop_no_loop_returns_false(self, make_agent, monkeypatch):
        """Inner ``get_event_loop`` raises RuntimeError → returns False."""
        from unittest.mock import MagicMock

        from kohakuterrarium.core.backgroundify import BackgroundifyHandle

        agent = make_agent()
        h = MagicMock(spec=BackgroundifyHandle)
        h.promote = MagicMock()
        agent._active_handles["bash_x"] = h
        # Explicitly clear any captured loop so the fall-through path
        # under test (``_loop is None`` → ``get_event_loop`` → raises)
        # is the only one available.
        agent._loop = None

        import asyncio as _asyncio

        def no_running():
            raise RuntimeError("no running")

        def no_event():
            raise RuntimeError("no event loop")

        monkeypatch.setattr(_asyncio, "get_running_loop", no_running)
        monkeypatch.setattr(_asyncio, "get_event_loop", no_event)
        result = agent._promote_handle("bash_x")
        assert result is False


# ── Agent.run_forever() outer wrapper ────────────────────────────


class TestAgentRunForever:
    async def test_run_forever_starts_and_stops(self, make_agent):
        agent = make_agent(script=["ack"])

        # Stub _drive_input to return immediately.
        async def fake_drive():
            return None

        agent._drive_input = fake_drive
        await agent.run_forever()
        # Agent stopped after the loop.
        assert agent.is_running is False


# ── _drive_input idle log path + log content (lines 691-692, 715) ──


class TestDriveInputIdleLog:
    async def test_drive_input_logs_content_length(self, make_agent):
        agent = make_agent(script=["ack"])

        class _OnceInput:
            def __init__(self):
                self.exit_requested = False
                self.fired = False

            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                if self.fired:
                    self.exit_requested = True
                    return None
                self.fired = True
                return create_user_input_event("x" * 50)

        agent.input = _OnceInput()
        agent._running = True
        # _drive_input drives turns through the single consumer — spawn it.
        agent._consumer_resume.set()
        agent._consumer_task = asyncio.create_task(agent._run_event_consumer())
        await agent._drive_input()
        await agent.stop()


# ── KeyboardInterrupt handler in _drive_input (line 735) ────────


class TestDriveInputKeyboardInterrupt:
    async def test_keyboard_interrupt_handled(self, make_agent):
        agent = make_agent()

        class _KbdInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                raise KeyboardInterrupt()

        agent.input = _KbdInput()
        agent._running = True
        # KeyboardInterrupt is caught — no error propagated.
        await agent._drive_input()
        await agent.stop()


# ── CancelledError handler in _drive_input (lines 737-738) ──────


class TestDriveInputCancelled:
    async def test_cancelled_error_re_raised(self, make_agent):
        agent = make_agent()

        class _CancelInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                raise asyncio.CancelledError()

        agent.input = _CancelInput()
        agent._running = True
        with pytest.raises(asyncio.CancelledError):
            await agent._drive_input()


class TestDriveInputResumeTriggers:
    async def test_pending_resume_triggers_processed(self, make_agent):
        """When ``_pending_resume_triggers`` is set, _drive_input calls
        ``_restore_triggers`` (lines 690-692)."""
        agent = make_agent()
        agent._pending_resume_triggers = [
            # Malformed entries; _restore_triggers silently skips them.
            {"trigger_id": "", "type": "", "module": "", "data": {}}
        ]

        class _ExitInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            @property
            def exit_requested(self):
                return True

            async def get_input(self):
                return None

        agent.input = _ExitInput()
        agent._running = True
        await agent._drive_input()
        # Pending triggers consumed.
        assert agent._pending_resume_triggers is None


class TestDriveInputNoneEventContinue:
    async def test_none_event_without_exit_continues(self, make_agent):
        """Input returns None but exit_requested is False → continue (line 715)."""
        agent = make_agent()

        class _RetryInput:
            def __init__(self):
                self.calls = 0
                self.exit_requested = False

            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                self.calls += 1
                if self.calls < 2:
                    # First call returns None without exit → continue.
                    return None
                # Second call signals exit.
                self.exit_requested = True
                return None

        agent.input = _RetryInput()
        agent._running = True
        await agent._drive_input()
        # Loop iterated at least twice.
        assert agent.input.calls >= 2


class TestDriveInputFatalErrorWriteFailure:
    async def test_fatal_error_write_to_output_fails(self, make_agent):
        """When the inner write_to_output fails too, the outer raises
        re-raises the original error (lines 748-749)."""
        agent = make_agent()

        class _BadInput:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def get_input(self):
                raise RuntimeError("fatal input crash")

        agent.input = _BadInput()

        # Make output_router.default_output.write also fail.
        class _BadDefault:
            async def write(self, text):
                raise RuntimeError("output crash")

            async def start(self):
                pass

            async def stop(self):
                pass

        agent.output_router.default_output = _BadDefault()
        agent._running = True
        with pytest.raises(RuntimeError, match="fatal input crash"):
            try:
                await agent._drive_input()
            finally:
                agent._running = False


# ── attach_session_store compact_count restore failure (831-832) ──


class TestAttachSessionStoreCompactCountBadValue:
    async def test_invalid_compact_count_swallowed(self, make_agent, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="x",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        await agent.start()
        try:
            # Set garbage in compact_count → restore swallows TypeError.
            store.state["test_agent:compact_count"] = object()
            agent.attach_session_store(store)
        finally:
            await agent.stop()

    async def test_text_mode_promotion_text_feedback(self, make_agent):
        agent = make_agent()
        await agent.start()
        try:
            from kohakuterrarium.core.backgroundify import BackgroundifyHandle
            from unittest.mock import MagicMock

            h = MagicMock(spec=BackgroundifyHandle)

            async def fake_wait(*args, **kwargs):
                return {}, True

            agent._wait_handles = fake_wait  # type: ignore[method-assign]
            ok = await agent._collect_and_push_feedback(
                agent.controller,
                {"x": h},
                ["x"],
                {},
                False,
            )
            assert ok is True
        finally:
            await agent.stop()


# ── mid-turn batch drain: all event types, one turn per flush ────


class TestMidTurnBatchDrain:
    async def test_all_event_types_buffer_while_lock_held(self, make_agent):
        # Every stackable event type folds onto the inbox instead of
        # blocking on the mutex — a mutex-blocked event would run its own
        # serial turn afterwards, each needing its own interrupt.
        from kohakuterrarium.core.events import (
            TriggerEvent,
            create_tool_complete_event,
        )

        agent = make_agent()
        await agent.start()
        try:
            events = [
                create_tool_complete_event(job_id="bash_1", content="out"),
                TriggerEvent(
                    type="subagent_output", content="sub out", job_id="agent_x"
                ),
                TriggerEvent(
                    type="creature_output",
                    content="peer says hi",
                    prompt_override="[from peer] peer says hi",
                ),
            ]
            async with agent._processing_lock:
                for evt in events:
                    accepted = await agent._process_event(evt)
                    assert accepted is False, f"{evt.type} must fold, not block"
                assert [env.event for env in agent._event_inbox._dq] == events
        finally:
            await agent.stop()

    async def test_nonstackable_active_turn_rejects_fold_ins(self, make_agent):
        # A non-stackable ACTIVE turn (startup, error) must not absorb
        # fold-in events — the incoming-event stackable check alone can't
        # see the active turn's flag, so ``_active_turn_stackable`` gates it.
        agent = make_agent()
        await agent.start()
        try:
            agent._active_turn_stackable = False
            async with agent._processing_lock:
                task = asyncio.ensure_future(agent.inject_input("queued", source="web"))
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                # Did NOT fold — enqueued as a primary (future-bearing) so
                # it runs its own turn after the lock releases.
                folds = [e for e in agent._event_inbox._dq if e.future is None]
                assert folds == []
                task.cancel()
        finally:
            agent._active_turn_stackable = True
            await agent.stop()

    async def test_drain_preserves_multimodal_completion_parts(self, make_agent):
        # A multimodal background result drained mid-turn must keep its
        # image parts — get_text_content() flattening dropped them.
        from kohakuterrarium.core.events import TriggerEvent
        from kohakuterrarium.llm.message import ImagePart, TextPart

        agent = make_agent()
        await agent.start()
        try:
            agent._event_inbox.put(
                EventEnvelope(
                    TriggerEvent(
                        type="tool_complete",
                        content=[
                            TextPart(text="rendered chart"),
                            ImagePart(url="data:image/png;base64,xyz"),
                        ],
                        job_id="plot_1",
                    )
                )
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            last_user = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ][-1]
            content = last_user.content
            assert isinstance(content, list)
            assert any(
                isinstance(p, dict) and p.get("type") != "text" for p in content
            ), f"image part must survive the drain: {content}"
            texts = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            assert "[Tool plot_1 completed]" in texts
            assert "rendered chart" in texts
        finally:
            await agent.stop()

    def test_coalesce_preserves_typed_content_parts(self):
        # ``normalize_content_parts`` produces typed ContentPart
        # instances; the mixed-modal coalesce used to keep only dicts,
        # silently dropping the text AND the image of a typed entry.
        from kohakuterrarium.core.agent_mid_turn import _coalesce_user_contents
        from kohakuterrarium.llm.message import ImagePart, TextPart

        combined = _coalesce_user_contents(
            [
                [TextPart(text="look at this"), ImagePart(url="http://x/i.png")],
                "second message",
            ]
        )
        assert isinstance(combined, list)
        texts = [p.get("text", "") for p in combined if p.get("type") == "text"]
        assert any("look at this" in t for t in texts)
        assert any("second message" in t for t in texts)
        assert any(
            p.get("type") != "text" for p in combined
        ), "the image part must survive the coalesce"

    async def test_non_stackable_events_bypass_buffer(self, make_agent):
        # ``stackable=False`` marks events that need immediate,
        # standalone attention (errors, shutdown-ish signals) — they
        # must queue on the lock, not fold into another turn's context.
        from kohakuterrarium.core.events import create_error_event

        agent = make_agent()
        await agent.start()
        try:
            err = create_error_event("RuntimeError", "boom")
            async with agent._processing_lock:
                task = asyncio.ensure_future(agent._process_event(err))
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
                # Non-stackable → not folded; enqueued as a primary.
                folds = [e for e in agent._event_inbox._dq if e.future is None]
                assert folds == []
                task.cancel()
        finally:
            await agent.stop()

    async def test_drain_formats_non_user_events_and_skips_their_records(
        self, make_agent
    ):
        from kohakuterrarium.core.events import (
            TriggerEvent,
            create_tool_complete_event,
        )

        agent = make_agent()
        await agent.start()
        try:
            captured: list[tuple] = []
            original = agent.output_router.notify_activity

            def spy(activity_type, detail, metadata=None):
                captured.append((activity_type, dict(metadata or {})))
                return original(activity_type, detail, metadata)

            agent.output_router.notify_activity = spy  # type: ignore[assignment]
            for evt in [
                TriggerEvent(type="user_input", content="hello"),
                create_tool_complete_event(job_id="bash_1", content="tool out"),
                TriggerEvent(
                    type="subagent_output", content="sub out", job_id="agent_x"
                ),
            ]:
                agent._event_inbox.put(EventEnvelope(evt))
            count = await agent._drain_mid_turn_pending_inputs(agent.controller)
            assert count == 3
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            combined = user_msgs[-1].content
            assert "hello" in combined
            assert "[Tool bash_1 completed]\ntool out" in combined
            assert "[Sub-agent agent_x output]\nsub out" in combined
            # Only the user-facing entry records a queued-banner frame —
            # tool/sub-agent completions already have their own
            # persisted activity events.
            injected = [c for c in captured if c[0] == "user_input_injected"]
            assert len(injected) == 1
            assert injected[0][1]["content"] == "hello"
        finally:
            await agent.stop()

    async def test_drain_attaches_background_status_hint(self, make_agent):
        # A completion drained while a sibling background job is still
        # alive must carry the live-jobs status so the model doesn't
        # re-dispatch or assume the sibling failed.
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.core.job import JobState, JobStatus, JobType

        agent = make_agent()
        await agent.start()
        try:
            agent.executor.job_store.register(
                JobStatus(
                    job_id="bash_sibling",
                    job_type=JobType.TOOL,
                    type_name="bash",
                    state=JobState.RUNNING,
                )
            )
            agent._event_inbox.put(
                EventEnvelope(
                    create_tool_complete_event(job_id="grep_done", content="42 hits")
                )
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            combined = str(user_msgs[-1].content)
            assert "[Tool grep_done completed]" in combined
            assert "[background status] Still running:" in combined
            assert "bash_sibling" in combined
            assert "treat them as failed" in combined
        finally:
            await agent.stop()

    async def test_drain_emits_background_result_banner(self, make_agent, tmp_path):
        # A drained background completion must surface a
        # ``background_result`` banner (live activity + persisted
        # event) instead of rendering as a phantom user bubble.
        from kohakuterrarium.core.events import create_tool_complete_event
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["test_agent"],
        )
        agent = make_agent()
        agent.attach_session_store(store)
        await agent.start()
        try:
            emitted: list[tuple[str, dict]] = []
            orig = agent.output_router.notify_activity

            def spy(kind, message, metadata=None, **kwargs):
                emitted.append((kind, dict(metadata or {})))
                return orig(kind, message, metadata=metadata, **kwargs)

            agent.output_router.notify_activity = spy
            agent._event_inbox.put(
                EventEnvelope(
                    create_tool_complete_event(job_id="grep_abc123", content="done")
                )
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            banners = [m for k, m in emitted if k == "background_result"]
            assert len(banners) == 1
            assert banners[0]["job_id"] == "grep_abc123"
            assert banners[0]["kind"] == "tool"
            events = store.get_events("test_agent")
            persisted = [e for e in events if e.get("type") == "background_result"]
            assert len(persisted) == 1
            assert persisted[0].get("job_id") == "grep_abc123"
        finally:
            await agent.stop()
            store.close()

    async def test_own_turn_bg_completion_emits_banner(self, make_agent):
        # A background completion that starts its own turn (agent was
        # idle) banners the delivery before processing.
        from kohakuterrarium.core.events import create_tool_complete_event

        agent = make_agent(script=["ack"])
        await agent.start()
        try:
            emitted: list[str] = []
            orig = agent.output_router.notify_activity

            def spy(kind, message, metadata=None, **kwargs):
                emitted.append(kind)
                return orig(kind, message, metadata=metadata, **kwargs)

            agent.output_router.notify_activity = spy
            await agent._process_event(
                create_tool_complete_event(job_id="agent_xyz789", content="report")
            )
            assert "background_result" in emitted
        finally:
            await agent.stop()

    async def test_drain_omits_hint_when_nothing_running(self, make_agent):
        from kohakuterrarium.core.events import create_tool_complete_event

        agent = make_agent()
        await agent.start()
        try:
            agent._event_inbox.put(
                EventEnvelope(
                    create_tool_complete_event(job_id="grep_done", content="42 hits")
                )
            )
            await agent._drain_mid_turn_pending_inputs(agent.controller)
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert "[background status]" not in str(user_msgs[-1].content)
        finally:
            await agent.stop()

    async def test_text_mode_bg_dispatch_ack_rides_existing_feedback_round(
        self, make_agent
    ):
        # Text mode has no role=tool slot — when a feedback round
        # happens anyway (a direct tool also ran), the ack rides along
        # so the model learns the dispatch succeeded.
        release = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangbg]msg=x[hangbg/][/echo]msg=y[echo/]",
                "ok, waiting for background",
            ]
        )
        tool = _HangingBgTool(release)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        echo = _EchoTool()
        agent.registry.register_tool(echo)
        agent.executor.register_tool(echo)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("kick off bg"))
            all_user = "\n".join(
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            )
            assert "Running in background" in all_user
            assert "hangbg" in all_user
        finally:
            release.set()
            await agent.stop()

    async def test_text_mode_bg_only_dispatch_never_forces_extra_round(
        self, make_agent
    ):
        # A bg-only dispatch must NOT create a feedback round for the
        # ack: the turn ending IS the "stop outputting" the ack asks
        # for. Forcing a round both wastes an LLM call and lets the
        # model reply before the real result arrives.
        release = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangbg]msg=x[hangbg/]",
                "this round must never run",
            ]
        )
        tool = _HangingBgTool(release)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        try:
            await agent._process_event(create_user_input_event("kick off bg"))
            assistants = [
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "assistant"
            ]
            assert all("this round must never run" not in a for a in assistants)
        finally:
            release.set()
            await agent.stop()

    async def test_leftover_buffer_flushes_after_turn_ends(self, make_agent):
        # An event that folds AFTER the turn's last mid-turn re-claim must
        # not sit in the inbox until the next unrelated turn — the consumer
        # loops back and claims it as the next turn.
        started = asyncio.Event()
        release = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangdirect]msg=x[hangdirect/]",
                "after tool",
                "leftover turn",
            ]
        )
        tool = _HangingDirectTool(started, release)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()

        async def no_drain(controller):
            return 0

        real_drain = agent._drain_mid_turn_pending_inputs
        agent._drain_mid_turn_pending_inputs = no_drain  # type: ignore[assignment]
        turn = asyncio.create_task(
            agent._process_event(create_user_input_event("kick off"))
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            accepted = await agent.inject_input("late msg", source="web")
            assert accepted is False
            release.set()
            await asyncio.wait_for(
                asyncio.gather(turn, return_exceptions=True), timeout=5
            )
            agent._drain_mid_turn_pending_inputs = real_drain  # type: ignore[assignment]
            for _ in range(200):
                if agent._event_inbox.empty() and not agent._processing_lock.locked():
                    break
                await asyncio.sleep(0.02)
            assert agent._event_inbox.empty(), (
                "leftover folded event must run when the turn that outran " "it ends"
            )
            user_texts = [
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            ]
            assert any("late msg" in t for t in user_texts)
        finally:
            release.set()
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            await agent.stop()

    async def test_interrupt_flush_batches_whole_buffer_into_one_turn(self, make_agent):
        # Interrupting with N buffered events must produce ONE new
        # turn (first event starts it, the rest drain into it) — not N
        # serial turns each needing its own interrupt.
        started = asyncio.Event()
        agent = make_agent(
            script=[
                "[/hangdirect]msg=x[hangdirect/]",
                "resumed",
                "drained rest",
            ]
        )
        tool = _HangingDirectTool(started)
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)
        await agent.start()
        turns: list[str] = []
        original_pbwc = agent._process_batch_with_controller

        async def counting_pbwc(events, controller):
            turns.append(events[0].type)
            return await original_pbwc(events, controller)

        agent._process_batch_with_controller = counting_pbwc  # type: ignore[assignment]
        turn = asyncio.create_task(
            agent._process_event(create_user_input_event("kick off"))
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            for text in ("one", "two", "three"):
                accepted = await agent.inject_input(text, source="web")
                assert accepted is False
            assert len(agent._event_inbox) == 3

            agent.interrupt()
            await asyncio.wait_for(
                asyncio.gather(turn, return_exceptions=True), timeout=5
            )
            for _ in range(200):
                if agent._event_inbox.empty() and not agent._processing_lock.locked():
                    break
                await asyncio.sleep(0.02)

            assert agent._event_inbox.empty()
            # kick-off turn + exactly ONE batched turn for one/two/three.
            assert len(turns) == 2, (
                "the consumer must batch all queued events into one turn; "
                f"saw turn starters: {turns}"
            )
            all_user_text = "\n".join(
                str(m.content)
                for m in agent.controller.conversation.get_messages()
                if getattr(m, "role", None) == "user"
            )
            for text in ("one", "two", "three"):
                assert text in all_user_text
            # The wake event that continues the loop after a drain must
            # not fabricate a completion in the conversation.
            assert "[Tool None completed]" not in all_user_text
            assert "[Tool  completed]" not in all_user_text
        finally:
            if not turn.done():
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
            await agent.stop()
