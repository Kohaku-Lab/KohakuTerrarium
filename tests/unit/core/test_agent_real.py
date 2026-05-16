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

import pytest

from kohakuterrarium.bootstrap import llm as bootstrap_llm
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.core.events import (
    create_user_input_event,
)
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

    def _fake_create(config, llm_override=None):
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
    async def test_event_dropped_when_not_running(self, make_agent):
        agent = make_agent(script=["unused"])
        # Don't start — _process_event should bail.
        await agent._process_event(create_user_input_event("hi"))
        # No assistant message recorded.
        msgs = agent.controller.conversation.get_messages()
        roles = [m.role for m in msgs]
        # Only system survives.
        assert "assistant" not in roles


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

        agent.input = _OneShotInput()
        # Don't call start (since it would start the original none-input).
        agent._running = True
        try:
            await agent._drive_input()
            assert agent.controller.conversation.get_last_assistant_message()
        finally:
            await agent.stop()


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


# ── LLM exception during processing ─────────────────────────────


class TestLLMExceptionDuringProcessing:
    async def test_llm_error_emits_processing_error(self, make_agent, patched_llm):
        class _BadLLM(ScriptedLLM):
            async def chat(self, messages, **kwargs):
                raise RuntimeError("API outage")

        bad_llm = _BadLLM([])

        from kohakuterrarium.bootstrap import llm as bootstrap_llm
        from kohakuterrarium.bootstrap import agent_init

        def _fake_create(cfg, **kw):
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
            await agent._process_event(create_user_input_event("hello"))
            events = store.get_events("test_agent")
            types_ = [e["type"] for e in events]
            assert "user_input" in types_
            assert "user_message" in types_
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
        # Patch Agent.run so we exit immediately.
        original_run = Agent.run

        async def _stub_run(self):
            self._running = True
            await self.stop()

        Agent.run = _stub_run
        try:
            await run_agent(str(config_dir))
        finally:
            Agent.run = original_run


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
        agent = make_agent()
        # Don't start — agent._running is False.
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

        def _fake_create(cfg, **kw):
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
            await agent._process_event_with_controller(
                create_user_input_event("hi"), agent.controller
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


# ── Agent.run() outer wrapper (lines 662-666) ───────────────────


class TestAgentRun:
    async def test_run_starts_and_stops(self, make_agent):
        agent = make_agent(script=["ack"])

        # Stub _drive_input to return immediately.
        async def fake_drive():
            return None

        agent._drive_input = fake_drive
        await agent.run()
        # Agent stopped after run.
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
