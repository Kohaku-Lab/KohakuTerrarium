"""Integration test for the ``modules/`` package — the extension protocols.

This is the comprehensive usage example for ``kohakuterrarium.modules``:
``input`` / ``trigger`` / ``tool`` / ``output`` / ``subagent`` /
``user_command`` / ``plugin``.  Every test here loads *real* module
implementations into a *real* :class:`Agent` and drives them the same
way the production runtime does:

* ``bootstrap/*`` builds the agent and wires modules into it.
* ``core/executor.py`` fires plugin ``pre_tool_execute`` /
  ``post_tool_execute`` around every tool call (and a
  ``PluginBlockError`` in a pre-hook becomes the tool result).
* ``core/trigger_manager.py`` runs each trigger's ``wait_for_trigger``
  loop and feeds the produced ``TriggerEvent`` into ``_process_event``.
* ``modules/subagent/manager.py`` spawns sub-agents; results route back
  into the parent controller's conversation.
* ``modules/output/router.py`` runs the parse-event state machine.
* ``bootstrap/agent_init.py`` wires slash commands; ``inject_input``
  with a ``/command`` string dispatches them against the live agent.

The ONLY seam is the LLM — both ``create_llm_provider`` import sites
are monkeypatched to :class:`ScriptedLLM`.  Everything else is the
real collaborator.

Each method runs ONE complete workflow end-to-end.
"""

import asyncio
from typing import Any

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
)
from kohakuterrarium.core.events import EventType, create_user_input_event
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.output.router_multi import MultiOutputRouter
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext as RuntimePluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    OutputTarget,
    SubAgentConfig,
)
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.modules.trigger.timer import TimerTrigger
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)
from kohakuterrarium.parsing import (
    AssistantImageEvent,
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.skills.registry import Skill
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

pytestmark = pytest.mark.timeout(30)


# ─────────────────────────────────────────────────────────────────────
# Real module implementations used by the workflows below.
# These are ordinary subclasses of the public ``modules/`` base
# classes — exactly what a framework user would write.
# ─────────────────────────────────────────────────────────────────────


class RecordingTool(BaseTool):
    """A real DIRECT-mode tool. Records every args dict it executes with
    so the test can prove plugin pre-hooks rewrote the input.

    When ``msg`` is ``"explode"`` it returns a ``ToolResult`` carrying an
    error, exercising the executor's error-feedback path."""

    def __init__(self) -> None:
        super().__init__()
        self.executed_with: list[dict[str, Any]] = []

    @property
    def tool_name(self) -> str:
        return "recorder"

    @property
    def description(self) -> str:
        return "Echo the 'msg' arg back as output."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        self.executed_with.append(dict(args))
        msg = args.get("msg", "")
        if msg == "explode":
            return ToolResult(
                error="recorder failed: deliberate explosion", exit_code=1
            )
        return ToolResult(output=f"recorder saw: {msg}")


class GuardrailPlugin(BasePlugin):
    """A real plugin exercising the FULL plugin hook surface.

    Tool hooks:
    * ``pre_tool_execute`` rewrites the ``msg`` arg when it is "rewrite-me".
    * ``pre_tool_execute`` raises ``PluginBlockError`` when ``msg`` is
      "forbidden" — the executor turns that into the tool result.
    * ``post_tool_execute`` appends a marker to the tool's output.

    LLM hooks:
    * ``pre_llm_call`` records the model name and message count.
    * ``post_llm_call`` records the finalized assistant response.

    Lifecycle callbacks:
    * ``on_load`` / ``on_unload`` — plugin install / teardown.
    * ``on_agent_start`` / ``on_agent_stop`` — agent lifecycle.
    * ``on_event`` — fire-and-forget observation of every trigger event.

    Prompt contribution:
    * ``get_prompt_content`` returns a banner spliced into the system prompt.
    """

    name = "guardrail"
    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self.pre_calls: list[str] = []
        self.post_calls: list[str] = []
        self.pre_llm_calls: list[tuple[str, int]] = []
        self.post_llm_responses: list[str] = []
        self.lifecycle: list[str] = []
        self.observed_events: list[str] = []

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        self.pre_calls.append(kwargs.get("tool_name", ""))
        msg = args.get("msg", "")
        if msg == "forbidden":
            raise PluginBlockError("guardrail blocked: 'forbidden' is not allowed")
        if msg == "rewrite-me":
            return {**args, "msg": "rewritten-by-plugin"}
        return None

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> Any | None:
        self.post_calls.append(kwargs.get("tool_name", ""))
        if hasattr(result, "output"):
            result.output = f"{result.output} [post-hook]"
            return result
        return None

    async def pre_llm_call(self, messages: list[dict], **kwargs: Any):
        self.pre_llm_calls.append((kwargs.get("model", ""), len(messages)))
        return None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs: Any
    ):
        self.post_llm_responses.append(response)
        return None

    def get_prompt_content(self, context: Any) -> str | None:
        return "GUARDRAIL-PLUGIN-BANNER: tool calls are policed."

    async def on_load(self, context: Any) -> None:
        self.lifecycle.append("on_load")

    async def on_unload(self) -> None:
        self.lifecycle.append("on_unload")

    async def on_agent_start(self) -> None:
        self.lifecycle.append("on_agent_start")

    async def on_agent_stop(self) -> None:
        self.lifecycle.append("on_agent_stop")

    async def on_event(self, event: Any) -> None:
        self.observed_events.append(getattr(event, "type", None))


class ScopedPlugin(BasePlugin):
    """A plugin gated by ``applies_to`` — it only applies to agents whose
    name is ``other_agent``, so against ``modules_agent`` every hook is
    skipped by the manager's ``_applicable_plugins`` filter."""

    name = "scoped"
    priority = 20
    applies_to = {"agent_names": ["other_agent"]}

    def __init__(self) -> None:
        super().__init__()
        self.pre_calls: list[str] = []

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        self.pre_calls.append(kwargs.get("tool_name", ""))
        return {**args, "msg": "SCOPED-SHOULD-NOT-RUN"}


class PingCommand(BaseUserCommand):
    """A real AGENT-layer slash command. Reads live agent state to prove
    the command runs against the constructed Agent, not a stub."""

    name = "ping"
    aliases = ["p"]
    description = "Reply pong with the live agent name."
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        agent_name = getattr(getattr(agent, "config", None), "name", "?")
        if args == "boom":
            return UserCommandResult(error="ping refused: 'boom' is not allowed")
        return UserCommandResult(
            output=f"pong from {agent_name}: {args}".strip(),
            consumed=True,
        )


class RewordCommand(BaseUserCommand):
    """A non-consuming slash command: it rewrites the user's input and
    lets the rewritten text flow through to the LLM as a real turn."""

    name = "reword"
    aliases: list[str] = []
    description = "Rewrite the input and pass it to the LLM."
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        return UserCommandResult(
            output=f"REWORDED: {args}",
            consumed=False,
        )


class _NotUniversalTrigger(BaseTrigger):
    """A trigger that did NOT opt into ``universal`` — CallableTriggerTool
    must refuse to wrap it as a tool."""

    universal = False

    async def wait_for_trigger(self):  # pragma: no cover — never started
        return None


class _UniversalNoNameTrigger(BaseTrigger):
    """``universal = True`` but with no ``setup_tool_name`` — the adapter
    rejects it because there is no tool name to expose."""

    universal = True
    setup_tool_name = ""

    async def wait_for_trigger(self):  # pragma: no cover — never started
        return None


# ─────────────────────────────────────────────────────────────────────
# Fixtures — patch the LLM seam, build a real Agent.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def llm_box(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites so every Agent
    (and every sub-agent the manager spawns) gets a deterministic
    :class:`ScriptedLLM`. The box lets a test set the script before
    constructing the agent."""

    class _Box:
        def __init__(self) -> None:
            self.script: list = ["OK"]

        def set(self, script: list) -> None:
            self.script = script

    box = _Box()

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(box.script)

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return box


@pytest.fixture
def make_agent(llm_box, tmp_path):
    """Build a real, fully-wired :class:`Agent` with stub I/O.

    The default output is swapped for an :class:`OutputRecorder` so the
    workflows can assert on observable output side effects.
    """

    def _build(
        *,
        script: list | None = None,
        named_outputs: dict[str, OutputConfigItem] | None = None,
    ) -> Agent:
        if script is not None:
            llm_box.set(script)
        cfg = AgentConfig(
            name="modules_agent",
            model="gpt-4",
            provider="openai",
            api_key_env="",
            system_prompt="You are an integration-test agent.",
            include_tools_in_prompt=True,
            include_hints_in_prompt=False,
            tool_format="bracket",
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(
                type="stdout",
                named_outputs=named_outputs or {},
            ),
        )
        agent = Agent(cfg)
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        agent._recorder = recorder
        return agent

    return _build


async def _settle(agent: Agent, *, until: Any = None, tries: int = 50) -> None:
    """Let the event loop drain the sub-agent task + the chained
    fire-and-forget ``_process_event`` task the background-completion
    callback (``_on_bg_complete``) schedules.

    The runtime dispatches sub-agents as background tasks, so the
    follow-up turn that delivers the result back to the parent runs on
    a *separate* task chain — the test has to yield long enough for it.
    ``until`` is an optional predicate; we stop early once it holds.
    """
    for _ in range(tries):
        if until is not None and until():
            return
        await asyncio.sleep(0.01)


# ─────────────────────────────────────────────────────────────────────
# The workflows.
# ─────────────────────────────────────────────────────────────────────


class TestModulesIntegration:
    """End-to-end workflows exercising each ``modules/`` protocol through
    a real :class:`Agent`."""

    async def test_plugin_hooks_wrap_a_real_tool_call(self, make_agent):
        """plugin protocol — the FULL hook surface fires through a real
        agent run: tool pre/post hooks (incl. arg rewrite + a
        ``PluginBlockError`` veto), LLM pre/post hooks, lifecycle
        callbacks (``on_load`` / ``on_agent_start`` / ``on_event`` /
        ``on_agent_stop`` / ``on_unload``), runtime prompt contribution,
        and ``applies_to`` declarative gating.

        Mirrors: ``core/executor.py`` wrapping ``tool.execute``,
        ``core/controller.py`` firing ``pre_llm_call`` /
        ``post_llm_call``, ``core/agent.py`` + ``agent_lifecycle`` +
        ``agent_handlers`` firing the lifecycle callbacks, and
        ``PluginManager._applicable_plugins`` enforcing ``applies_to``.
        """
        # The controller loops within ONE _process_event until a turn
        # produces no tool call — so each turn here is a tool-call
        # response followed by a plain-text wrap-up.
        agent = make_agent(
            script=[
                # Turn 1 (input "run rewrite"): tool call, then wrap-up.
                ScriptEntry(
                    "[/recorder]@@msg=rewrite-me\n[recorder/]", match="rewrite"
                ),
                ScriptEntry("rewrite turn done", match="rewritten-by-plugin"),
                # Turn 2 (input "run forbidden"): blocked tool, then wrap-up.
                # ``match`` must be a substring UNIQUE to the user input —
                # ``"forbidden"`` alone also appears in the block-error tool
                # result ("guardrail blocked: 'forbidden' is not allowed"),
                # which would re-select this tool-call entry on the wrap-up
                # turn and loop the controller forever.
                ScriptEntry(
                    "[/recorder]@@msg=forbidden\n[recorder/]", match="run forbidden"
                ),
                ScriptEntry("forbidden turn done", match="guardrail blocked"),
            ]
        )
        tool = RecordingTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)

        plugin = GuardrailPlugin()
        scoped = ScopedPlugin()  # gated OUT — agent name does not match.
        mgr = PluginManager()
        mgr.register(plugin)
        mgr.register(scoped)
        agent.plugins = mgr
        agent.controller.plugins = mgr
        # load_all wires the load-context so ``applies_to`` gating is
        # live AND fires the ``on_load`` lifecycle callback.
        ctx = RuntimePluginContext(agent_name="modules_agent", _host_agent=agent)
        await mgr.load_all(ctx)
        assert "on_load" in plugin.lifecycle

        # Runtime prompt contributions are collected only from applicable
        # plugins — guardrail contributes, the gated-out scoped plugin
        # does not.
        contributions = mgr.collect_prompt_contributions(ctx)
        assert "GUARDRAIL-PLUGIN-BANNER: tool calls are policed." in contributions
        assert all("SCOPED" not in c for c in contributions)

        await agent.start()
        # on_agent_start fired on agent.start().
        assert "on_agent_start" in plugin.lifecycle
        try:
            # Turn 1: pre-hook rewrites the arg before the tool runs.
            await agent._process_event(create_user_input_event("run rewrite"))
            assert tool.executed_with == [{"msg": "rewritten-by-plugin"}]
            assert plugin.pre_calls == ["recorder"]
            assert plugin.post_calls == ["recorder"]
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # post_tool_execute appended its marker to the output that
            # was fed back to the controller.
            assert "recorder saw: rewritten-by-plugin [post-hook]" in convo_text

            # LLM hooks fired around BOTH turns of this _process_event:
            # the tool-call turn and the wrap-up turn. Each pre-hook saw
            # a non-empty message list.
            assert len(plugin.pre_llm_calls) == 2
            assert all(count > 0 for _model, count in plugin.pre_llm_calls)
            assert "rewrite turn done" in plugin.post_llm_responses
            # on_event observed the user_input trigger event.
            assert EventType.USER_INPUT in plugin.observed_events

            # The ``applies_to``-gated plugin's pre_tool_execute NEVER
            # ran — the manager filtered it out before the hook chain.
            assert scoped.pre_calls == []
            assert tool.executed_with == [{"msg": "rewritten-by-plugin"}]

            # Turn 2: pre-hook raises PluginBlockError -> the executor
            # converts it into the tool result; the real _execute never
            # runs at all for the forbidden arg.
            await agent._process_event(create_user_input_event("run forbidden"))
            assert tool.executed_with == [{"msg": "rewritten-by-plugin"}]
            # pre_tool_execute fired for the blocked call too (and raised);
            # post_tool_execute did NOT (the tool never executed).
            assert plugin.pre_calls == ["recorder", "recorder"]
            assert plugin.post_calls == ["recorder"]
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "guardrail blocked: 'forbidden' is not allowed" in convo_text
            # Four LLM pre-hook fires total across the two _process_event
            # calls (two turns each).
            assert len(plugin.pre_llm_calls) == 4

            # ── PluginContext accessor surface ──
            # The load-context is a real PluginContext wired to the live
            # agent — every typed accessor resolves to the agent's own
            # collaborator, not a stub.
            assert ctx.host_agent is agent
            assert ctx.registry is agent.registry
            assert ctx.controller is agent.controller
            assert ctx.subagent_manager is agent.subagent_manager
            assert ctx.scratchpad is agent.scratchpad
            assert ctx.compact_manager is agent.compact_manager
            # No session store attached in this harness → None, not a crash.
            assert ctx.session_store is None
            assert ctx.session_memory is None
            # get_state / set_state degrade to no-op without a store.
            ctx.set_state("k", "v")
            assert ctx.get_state("k") is None
            # inject_message_before_llm queues onto the controller.
            ctx.inject_message_before_llm("user", "INJECTED-PREAMBLE")
            assert agent.controller._pending_injections == [
                {"role": "user", "content": "INJECTED-PREAMBLE"}
            ]
            # inject_event pushes a TriggerEvent straight into the queue.
            before_q = agent.controller._event_queue.qsize()
            ctx.inject_event(create_user_input_event("queued-by-plugin"))
            assert agent.controller._event_queue.qsize() == before_q + 1

            # ── Manager introspection + enable/disable surface ──
            # Both plugins are registered; list_plugins reports them with
            # enabled flags, ordered by priority (guardrail=10 < scoped=20).
            listed = mgr.list_plugins()
            assert [p["name"] for p in listed] == ["guardrail", "scoped"]
            assert all(p["enabled"] for p in listed)
            assert mgr.get_plugin("guardrail") is plugin
            assert mgr.get_plugin("no_such") is None
            assert len(mgr) == 2 and bool(mgr) is True
            # Disable then re-enable the guardrail plugin.
            assert mgr.disable("guardrail") is True
            assert mgr.is_enabled("guardrail") is False
            assert plugin not in mgr._applicable_plugins()
            assert mgr.enable("guardrail") is True
            assert mgr.is_enabled("guardrail") is True
            # Disabling an unknown plugin returns False.
            assert mgr.disable("ghost") is False
            # list_plugins_with_options surfaces the (empty) option schema.
            with_opts = mgr.list_plugins_with_options()
            assert {p["name"] for p in with_opts} == {"guardrail", "scoped"}
            assert all(p["schema"] == {} and p["options"] == {} for p in with_opts)
            # set_plugin_options on an unknown plugin raises KeyError.
            with pytest.raises(KeyError):
                mgr.set_plugin_options("ghost", {})
            # runtime_services + termination checkers: neither plugin
            # contributes any, so both collectors come back empty.
            assert mgr.collect_runtime_services(ctx) == {}
            assert mgr.collect_termination_checkers() == []
            assert mgr.collect_commands() == []
            # should_proceed with no veto hooks → True (nothing vetoes).
            assert (
                await mgr.should_proceed("on_compact_start", context_length=10) is True
            )
        finally:
            await agent.stop()
        # on_agent_stop fired during agent.stop().
        assert "on_agent_stop" in plugin.lifecycle
        # Explicit teardown fires on_unload.
        await mgr.unload_all()
        assert plugin.lifecycle[-1] == "on_unload"

    async def test_trigger_produces_event_and_drives_a_turn(self, make_agent):
        """trigger protocol — a real :class:`TimerTrigger` is hot-plugged
        onto the running agent, its ``wait_for_trigger`` produces a
        ``TriggerEvent``, the :class:`TriggerManager` feeds it into
        ``_process_event``, the controller runs a turn off it; then a
        :class:`SchedulerTrigger` is installed via the universal
        :class:`CallableTriggerTool` (a real LLM-driven tool call),
        and its resume-dict round-trips.

        Mirrors: ``core/trigger_manager.py._run_loop`` →
        ``Agent._process_event`` and
        ``modules/trigger/callable.py.CallableTriggerTool``.
        """
        # Turn 2 installs a scheduler via the universal trigger tool.
        agent = make_agent(
            script=[
                ScriptEntry("timer acknowledged", match="heartbeat check"),
                ScriptEntry(
                    "[/add_schedule]@@every_minutes=30\n@@prompt=cron tick\n"
                    "[add_schedule/]",
                    match="install a schedule",
                ),
                ScriptEntry("schedule installed", match="trigger_id="),
            ]
        )
        # Register the universal CallableTriggerTool for SchedulerTrigger
        # exactly as the bootstrap layer exposes setup-able triggers.
        sched_tool = CallableTriggerTool(SchedulerTrigger)
        assert sched_tool.tool_name == "add_schedule"
        assert sched_tool.description.startswith("**Trigger** —")
        agent.registry.register_tool(sched_tool)
        agent.executor.register_tool(sched_tool)

        fired: list = []
        original = agent._process_event

        async def _spy(event):
            fired.append(event)
            return await original(event)

        agent._process_event = _spy  # observe events the manager delivers
        agent.trigger_manager._process_event = _spy

        await agent.start()
        try:
            # immediate=True so the first wait_for_trigger fires at once.
            trigger = TimerTrigger(
                interval=100.0, prompt="heartbeat check", immediate=True
            )
            trigger_id = await agent.add_trigger(trigger, trigger_id="hb")
            assert trigger_id == "hb"

            # Give the trigger manager's loop task a chance to fire +
            # deliver the event into _process_event.
            for _ in range(20):
                if fired:
                    break
                await asyncio.sleep(0.02)

            assert len(fired) == 1
            event = fired[0]
            assert event.type == EventType.TIMER
            assert event.content == "heartbeat check"

            # The controller ran a real turn off the trigger event.
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
            assert "timer acknowledged" in last.get_text_content()

            # The TimerTrigger serialises for session persistence.
            assert trigger.to_resume_dict()["prompt"] == "heartbeat check"

            # Hot-unplug the trigger — the manager stops + drops it.
            removed = await agent.remove_trigger("hb")
            assert removed is True
            assert agent.trigger_manager.get("hb") is None

            # Now drive a real turn where the LLM installs a SchedulerTrigger
            # via the universal CallableTriggerTool. The tool validates args,
            # builds the trigger, and registers it with the TriggerManager.
            ids_before = {ti.trigger_id for ti in agent.trigger_manager.list()}
            await agent._process_event(
                create_user_input_event("install a schedule please")
            )
            ids_after = {ti.trigger_id for ti in agent.trigger_manager.list()}
            new_ids = ids_after - ids_before
            assert len(new_ids) == 1
            new_id = next(iter(new_ids))
            installed = agent.trigger_manager._triggers[new_id]
            assert isinstance(installed, SchedulerTrigger)
            # Bracket-format args arrive as strings; the tool forwards them
            # verbatim into the trigger constructor.
            assert installed.every_minutes == "30"
            assert installed.prompt == "cron tick"

            # The installed scheduler's resume-dict round-trips back to an
            # equivalent trigger — the contract session persistence relies on.
            resumed = SchedulerTrigger.from_resume_dict(installed.to_resume_dict())
            assert resumed.every_minutes == "30"
            assert resumed.prompt == "cron tick"

            # ── SchedulerTrigger clock math: each mode computes a positive
            # wait that lands within its window. These are pure functions
            # the scheduler's wait_for_trigger relies on. ──
            every = SchedulerTrigger(every_minutes=30, prompt="p")
            wait_every = every._seconds_until_next()
            # Next 30-min slot is at most 30 minutes away, strictly future.
            assert 0 < wait_every <= 30 * 60
            hourly = SchedulerTrigger(hourly_at=15, prompt="p")
            wait_hourly = hourly._seconds_until_next()
            assert 0 < wait_hourly <= 60 * 60
            daily = SchedulerTrigger(daily_at="03:30", prompt="p")
            wait_daily = daily._seconds_until_next()
            assert 0 < wait_daily <= 24 * 60 * 60
            # No mode set → 60s fallback.
            assert SchedulerTrigger(prompt="p")._seconds_until_next() == 60

            # ── CallableTriggerTool error surface (real ToolContext) ──
            # Build the tool's parameter schema — the adapter injects an
            # optional ``name`` arg in front of the trigger class's schema.
            schema = sched_tool.get_parameters_schema()
            assert "name" in schema["properties"]
            assert "every_minutes" in schema["properties"]
            assert schema["properties"]["prompt"]["type"] == "string"
            assert "prompt" in schema["required"]
            # Full documentation renders the trigger-tool header + params.
            full_doc = sched_tool.get_full_documentation()
            assert "# add_schedule" in full_doc
            assert "**Trigger tool.**" in full_doc
            assert "- `prompt` (string) (required)" in full_doc

            # Calling _execute with no ToolContext → clean error result.
            no_ctx = await sched_tool._execute({"prompt": "x"}, context=None)
            assert no_ctx.exit_code == 1
            assert "requires a ToolContext" in no_ctx.error
            # Missing the required ``prompt`` arg → validation error result.
            tool_ctx = ToolContext(
                agent_name=agent.config.name,
                session=getattr(agent, "session", None),
                working_dir=agent.config.agent_path,
                agent=agent,
            )
            missing = await sched_tool._execute({"every_minutes": 5}, context=tool_ctx)
            assert missing.exit_code == 1
            assert "Missing required arg(s)" in missing.error
            assert "prompt" in missing.error
            # A valid call through _execute installs a real trigger and
            # returns a confirmation carrying the trigger id.
            ok = await sched_tool._execute(
                {"name": "explicit-id", "every_minutes": 10, "prompt": "ten-min tick"},
                context=tool_ctx,
            )
            assert ok.exit_code == 0
            assert ok.metadata["trigger_id"] == "explicit-id"
            assert "explicit-id" in ok.output
            installed2 = agent.trigger_manager._triggers["explicit-id"]
            assert isinstance(installed2, SchedulerTrigger)
            # Re-using a trigger_id that's already registered → error result.
            dup = await sched_tool._execute(
                {"name": "explicit-id", "every_minutes": 10, "prompt": "dup"},
                context=tool_ctx,
            )
            assert dup.exit_code == 1
            assert "explicit-id" in dup.error

            # ── CallableTriggerTool constructor guards ──
            # A trigger class that is not ``universal`` cannot be exposed.
            with pytest.raises(ValueError, match="not universal"):
                CallableTriggerTool(_NotUniversalTrigger)
            # A universal class with no ``setup_tool_name`` is rejected too.
            with pytest.raises(ValueError, match="setup_tool_name"):
                CallableTriggerTool(_UniversalNoNameTrigger)
        finally:
            await agent.stop()

    async def test_subagent_dispatch_and_routing(self, make_agent):
        """subagent protocol — a real :class:`SubAgentConfig` is
        registered on the agent, the controller dispatches it via a
        ``[/...]`` block, the :class:`SubAgentManager` spawns it, the
        sub-agent runs its OWN controller loop, and its result routes
        back into the parent.

        Mirrors: ``_dispatch_subagent_event`` → ``spawn_from_event`` →
        background completion → ``_on_bg_complete`` → follow-up
        ``_process_event``.
        """
        # ScriptedLLM matches: the sub-agent's user message contains the
        # task text "summarize the logs"; the parent's turns don't.
        script = [
            ScriptEntry("[/worker]\nsummarize the logs[worker/]", match="dispatch"),
            ScriptEntry("worker: logs look healthy", match="summarize the logs"),
            ScriptEntry("parent: worker finished", match="worker"),
            # Programmatic-spawn turn: the sub-agent answers its own task.
            ScriptEntry("worker: direct task done", match="direct programmatic task"),
        ]
        agent = make_agent(script=script)

        worker_cfg = SubAgentConfig(
            name="worker",
            description="Background worker sub-agent.",
            tools=[],
            system_prompt="You are a worker. Do the task and report back.",
            output_to=OutputTarget.CONTROLLER,
        )
        agent.subagent_manager.register(worker_cfg)
        agent.registry.register_subagent("worker", worker_cfg)
        assert "worker" in agent.subagent_manager.list_subagents()
        # The registered config is retrievable + projects a SubAgentInfo.
        assert agent.subagent_manager.get_config("worker") is worker_cfg
        info = agent.subagent_manager.get_subagent_info("worker")
        assert info is not None and info.description == "Background worker sub-agent."

        await agent.start()
        try:
            await agent._process_event(create_user_input_event("dispatch the worker"))
            # The sub-agent runs as a background task; the result routes
            # back via a chained _process_event. Settle until the parent
            # controller's wrap-up turn has landed.
            await _settle(
                agent,
                until=lambda: any(
                    "worker finished" in m.get_text_content()
                    for m in agent.controller.conversation.get_messages()
                ),
            )

            # The sub-agent ran its own loop and produced a result.
            results = list(agent.subagent_manager._results.values())
            assert len(results) == 1
            sa_result = results[0]
            assert sa_result.success is True
            assert "logs look healthy" in sa_result.output

            # The result routed back into the parent: a follow-up turn
            # ran and the parent controller saw the worker output.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "logs look healthy" in convo_text
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
            assert "worker finished" in last.get_text_content()

            # ── Programmatic spawn path (background=False) ──
            # The same SubAgentManager also drives a direct spawn: the
            # caller awaits completion and the JobStore tracks it.
            job_id = await agent.subagent_manager.spawn(
                "worker", "direct programmatic task", background=False
            )
            direct_result = agent.subagent_manager.get_result(job_id)
            assert direct_result is not None
            assert direct_result.success is True
            assert "direct task done" in direct_result.output
            # The job is recorded in the shared JobStore as DONE.
            status = agent.subagent_manager.get_status(job_id)
            assert status is not None
            assert status.state.value == "done"
            # wait_for on an already-finished job returns the cached result.
            again = await agent.subagent_manager.wait_for(job_id)
            assert again is direct_result

            # ── Error path: spawning an unregistered sub-agent ──
            with pytest.raises(ValueError, match="not registered"):
                await agent.subagent_manager.spawn("ghost", "noop")

            # Cleanup drops the finished job's bookkeeping but keeps results.
            agent.subagent_manager.cleanup(job_id)
            assert job_id not in agent.subagent_manager._tasks
            assert agent.subagent_manager.get_result(job_id) is direct_result

            # ── SubAgentConfig data surface (real config objects) ──
            # An explicit system_prompt is a full override — load_prompt
            # returns it verbatim even when extra_prompt is also set.
            override_cfg = SubAgentConfig(
                name="ovr",
                system_prompt="EXPLICIT OVERRIDE PROMPT",
                extra_prompt="should-not-append",
            )
            assert override_cfg.load_prompt() == "EXPLICIT OVERRIDE PROMPT"
            # With no system_prompt, the base default + extra are composed.
            extra_cfg = SubAgentConfig(name="helper", extra_prompt="BE CONCISE.")
            composed = extra_cfg.load_prompt()
            assert composed.startswith("You are a helper sub-agent.")
            assert "## Additional Instructions" in composed
            assert "BE CONCISE." in composed
            # from_dict coerces enum strings + filters unknown keys to extra.
            from_dict_cfg = SubAgentConfig.from_dict(
                {
                    "name": "fromdict",
                    "output_to": "external",
                    "context_mode": "queue_append",
                    "modifying_tools": ["write", "edit"],
                    "unknown_key": "lands-in-extra",
                }
            )
            assert from_dict_cfg.output_to is OutputTarget.EXTERNAL
            assert from_dict_cfg.context_mode is ContextUpdateMode.QUEUE_APPEND
            assert from_dict_cfg.modifying_tools == {"write", "edit"}
            assert from_dict_cfg.extra == {"unknown_key": "lands-in-extra"}
            # to_dict round-trips the enums back to their string values.
            as_dict = from_dict_cfg.to_dict()
            assert as_dict["output_to"] == "external"
            assert as_dict["context_mode"] == "queue_append"
            assert as_dict["modifying_tools"] == ["edit", "write"]

            # ── Manager prompt projection + depth guard ──
            # get_subagents_prompt lists every registered config.
            sa_prompt = agent.subagent_manager.get_subagents_prompt()
            assert "## Available Sub-Agents" in sa_prompt
            assert "- worker: Background worker sub-agent." in sa_prompt
            # A manager at its depth limit refuses to spawn — the error
            # surfaces as a failed SubAgentResult, not an exception.
            agent.subagent_manager._current_depth = 3
            agent.subagent_manager._max_depth = 3
            depth_job = await agent.subagent_manager.spawn(
                "worker", "too deep", background=False
            )
            depth_result = agent.subagent_manager.get_result(depth_job)
            assert depth_result is not None and depth_result.success is False
            assert "depth limit reached" in depth_result.error
            depth_status = agent.subagent_manager.get_status(depth_job)
            assert depth_status.state.value == "error"
            agent.subagent_manager._current_depth = 0
        finally:
            await agent.stop()

    async def test_interactive_subagent_stays_alive(self, make_agent):
        """subagent protocol — an interactive sub-agent (``interactive:
        true``) is started through the real :class:`SubAgentManager`,
        stays alive across multiple ``push_context`` calls, and produces
        output for each, then is stopped.

        Mirrors: ``modules/subagent/interactive_mgr.py`` +
        ``modules/subagent/interactive.py`` lifecycle.
        """
        agent = make_agent(script=["interactive reply"])

        interactive_cfg = SubAgentConfig(
            name="watcher",
            description="Interactive watcher.",
            tools=[],
            system_prompt="You are a watcher. React to each context update.",
            interactive=True,
            stateless=False,
        )
        agent.subagent_manager.register(interactive_cfg)
        # A second interactive sub-agent on a DIFFERENT context-update
        # mode (QUEUE_APPEND vs the default INTERRUPT_RESTART) — both
        # context modes must stay alive across updates.
        queue_cfg = SubAgentConfig(
            name="queuer",
            description="Queue-append watcher.",
            tools=[],
            system_prompt="You are a queuer. Process updates in order.",
            interactive=True,
            stateless=False,
            context_mode=ContextUpdateMode.QUEUE_APPEND,
        )
        agent.subagent_manager.register(queue_cfg)
        # A plain (non-interactive) config — start_interactive must reject it.
        agent.subagent_manager.register(
            SubAgentConfig(name="plain", description="Not interactive.", tools=[])
        )

        await agent.start()
        try:
            outputs: list = []
            sub = await agent.subagent_manager.start_interactive(
                "watcher", on_output=outputs.append
            )
            # The sub-agent is alive and listed.
            assert sub.is_active is True
            assert "watcher" in agent.subagent_manager.list_interactive()

            # Push two context updates — it stays alive across both and
            # fires the on_output callback once per processed update.
            await agent.subagent_manager.push_context("watcher", {"event": "first"})
            await _settle(agent, until=lambda: len(outputs) >= 1)
            assert sub.is_active is True
            await agent.subagent_manager.push_context("watcher", {"event": "second"})
            await _settle(agent, until=lambda: len(outputs) >= 2)

            # Both context updates were processed by the SAME live
            # instance — proof it stayed alive between updates.
            assert len(outputs) == 2
            assert all(o.is_complete for o in outputs)
            assert agent.subagent_manager.get_interactive("watcher") is sub
            assert sub.is_active is True

            # ── QUEUE_APPEND-mode interactive sub-agent ──
            queue_outputs: list = []
            queuer = await agent.subagent_manager.start_interactive(
                "queuer", on_output=queue_outputs.append
            )
            assert queuer.config.context_mode is ContextUpdateMode.QUEUE_APPEND
            assert set(agent.subagent_manager.list_interactive()) == {
                "watcher",
                "queuer",
            }
            # push_context_all fans one update to BOTH live instances.
            await agent.subagent_manager.push_context_all({"event": "broadcast"})
            await _settle(
                agent,
                until=lambda: len(outputs) >= 3 and len(queue_outputs) >= 1,
            )
            assert len(outputs) == 3  # watcher saw the broadcast too
            assert len(queue_outputs) >= 1
            # The QUEUE_APPEND instance processed its update and emitted a
            # completion signal — proof it stayed alive in its own mode.
            assert any(o.is_complete for o in queue_outputs)

            # ── Error paths ──
            with pytest.raises(ValueError, match="not interactive"):
                await agent.subagent_manager.start_interactive("plain")
            with pytest.raises(ValueError, match="not running"):
                await agent.subagent_manager.push_context("ghost", {"x": 1})

            # Explicit stop tears watcher down; stop_all_interactive sweeps
            # the rest.
            await agent.subagent_manager.stop_interactive("watcher")
            assert sub.is_active is False
            assert "watcher" not in agent.subagent_manager.list_interactive()
            await agent.subagent_manager.stop_all_interactive()
            assert agent.subagent_manager.list_interactive() == []
            assert queuer.is_active is False
        finally:
            await agent.stop()

    async def test_output_router_state_machine_routes_parse_events(self):
        """output protocol — the :class:`OutputRouter` state machine
        routes every :class:`ParseEvent` variant to the correct sink:
        plain text → default output, ``[/output_<name>]`` blocks → the
        named output module, tool/subagent/command blocks suppress raw
        text while open, secondary outputs receive a copy of ALL text,
        an unknown output target degrades to the default output, and
        the typed-event ``emit()`` bus fans activity + processing
        events.

        Mirrors: ``modules/output/router_parsing.py.route``, the
        ``OutputState`` suppression machine, and ``OutputRouter.emit``
        that ``core/agent_*`` drives during a turn.
        """
        default = OutputRecorder()
        side_channel = OutputRecorder()
        secondary = OutputRecorder()  # observer (e.g. SessionOutput)
        router = OutputRouter(
            default_output=default,
            named_outputs={"side": side_channel},
        )
        router.add_secondary(secondary)
        assert router.get_output_targets() == ["side"]
        await router.start()
        try:
            # 1. Plain text in NORMAL state -> default output.
            await router.route(TextEvent("hello "))

            # 2. A tool block opens: raw text inside is suppressed from
            #    the default output (suppress_tool_blocks defaults True).
            assert router.state.name == "NORMAL"
            await router.route(BlockStartEvent(block_type="tool"))
            assert router.state.name == "TOOL_BLOCK"
            await router.route(TextEvent("@@msg=ignored"))
            await router.route(
                ToolCallEvent(name="recorder", args={"msg": "ignored"}, raw="")
            )
            await router.route(BlockEndEvent(block_type="tool"))

            # 3. Back to NORMAL -> text flows to default again.
            await router.route(TextEvent("world"))

            # 4. A SUB-AGENT block: raw text inside is suppressed from the
            #    default output, the structured SubAgentCallEvent queues.
            await router.route(BlockStartEvent(block_type="subagent"))
            assert router.state.name == "SUBAGENT_BLOCK"
            await router.route(TextEvent("delegate this"))
            await router.route(
                SubAgentCallEvent(name="explore", args={"task": "scan"}, raw="")
            )
            await router.route(BlockEndEvent(block_type="subagent"))

            # 5. An explicit named-output block routes to that module.
            await router.route(
                OutputCallEvent(target="side", content="for the side channel", raw="")
            )

            # 6. An UNKNOWN output target degrades gracefully to the
            #    default output (prefixed) instead of raising.
            await router.route(
                OutputCallEvent(target="nowhere", content="orphaned content", raw="")
            )

            # 7. A command block: text inside is fully swallowed.
            await router.route(BlockStartEvent(block_type="command"))
            assert router.state.name == "COMMAND_BLOCK"
            await router.route(TextEvent("##info recorder##"))
            await router.route(CommandEvent(command="info", args="recorder", raw=""))
            await router.route(BlockEndEvent(block_type="command"))

            await router.flush()

            # Default output saw ONLY the NORMAL-state text — not the
            # tool-block body, not the subagent-block body, not the
            # command-block body.
            assert default.stream_text == "hello world"
            # The unknown-target content fell through to the default
            # output's write() path with an ``[output_<target>]`` prefix.
            assert default.writes == ["[output_nowhere] orphaned content"]
            # Secondary outputs receive a COPY of every text chunk,
            # regardless of suppression state — they're observers.
            assert secondary.stream_text == (
                "hello @@msg=ignoredworlddelegate this##info recorder##"
            )
            # The named output received exactly the routed block.
            assert side_channel.writes == ["for the side channel"]
            # The router queued the structured tool + subagent + command
            # events for the caller (the agent) to dispatch.
            assert [e.name for e in router.pending_tool_calls] == ["recorder"]
            assert [e.name for e in router.pending_subagent_calls] == ["explore"]
            assert [c.command for c in router.pending_commands] == ["info"]
            # And tracked BOTH completed named-outputs for controller
            # feedback — the real "side" target plus the degraded one.
            completed = router.get_and_clear_completed_outputs()
            assert len(completed) == 2
            assert completed[0].target == "side"
            assert completed[0].success is True
            assert completed[1].target == "nowhere(default)"
            assert completed[1].success is True
            # get_and_clear actually cleared the list.
            assert router.get_and_clear_completed_outputs() == []

            # 8. Typed-event bus: emit() fans activity + processing events
            #    to the default + secondary outputs.
            await router.emit(OutputEvent(type="processing_start"))
            await router.emit(
                OutputEvent(type="tool_start", content="recorder running")
            )
            await router.emit(OutputEvent(type="processing_end"))
            assert default.processing_starts == 1
            assert default.processing_ends == 1
            assert ("tool_start", "recorder running") in [
                (a.activity_type, a.detail) for a in default.activities
            ]
            # Secondary saw the activity event too.
            assert "tool_start" in secondary.activity_types()

            # 9. reset() clears pending state + returns to NORMAL.
            await router.route(BlockStartEvent(block_type="tool"))
            router.reset()
            assert router.state.name == "NORMAL"

            # 10. An AssistantImageEvent fans to the default + every
            #     secondary output's on_assistant_image hook. OutputRecorder
            #     inherits the BaseOutputModule no-op, so we just prove the
            #     route() dispatch doesn't raise and stays in NORMAL.
            await router.route(
                AssistantImageEvent(
                    url="https://img.example/cat.png",
                    detail="high",
                    source_type="generated",
                    source_name="dalle",
                    revised_prompt="a cat",
                )
            )
            assert router.state.name == "NORMAL"

            # 11. emit("text") drives the text state machine — the chunk
            #     lands on the default output's stream and the secondary's.
            default.clear_all()
            secondary.clear_all()
            await router.emit(OutputEvent(type="text", content="emitted-text"))
            assert default.stream_text == "emitted-text"
            assert secondary.stream_text == "emitted-text"
            # emit("assistant_image") routes through _handle_assistant_image.
            await router.emit(
                OutputEvent(
                    type="assistant_image",
                    payload={"url": "https://img.example/dog.png"},
                )
            )
            # emit("user_input") + emit("resume_batch") reach the default
            # output's on_user_input / on_resume hooks (no-ops on the
            # recorder, but the dispatch path is exercised).
            await router.emit(OutputEvent(type="user_input", content="hi there"))
            await router.emit(
                OutputEvent(type="resume_batch", payload={"events": [{"type": "x"}]})
            )

            # 12. get_output_feedback formats the completed-output records.
            #     Route a named-output block then read the feedback line.
            await router.route(
                OutputCallEvent(target="side", content="feedback content", raw="")
            )
            feedback = router.get_output_feedback()
            assert feedback is not None
            assert feedback.startswith("## Outputs Sent")
            assert "[side]" in feedback
            assert "feedback content" in feedback
            # Calling it again with nothing pending returns None.
            assert router.get_output_feedback() is None

            # 13. pending_outputs / completed_outputs property accessors.
            assert router.pending_outputs == []
            assert router.completed_outputs == []
        finally:
            await router.stop()

        # ── BaseOutputModule.emit default dispatch (real recorder) ──
        # OutputRecorder.emit() forwards to BaseOutputModule.emit, whose
        # match/case fans each typed event to the legacy hooks. Drive one
        # of each so the default switch is fully exercised.
        rec = OutputRecorder()
        await rec.emit(OutputEvent(type="text", content="base-text"))
        await rec.emit(OutputEvent(type="processing_start"))
        await rec.emit(OutputEvent(type="processing_end"))
        await rec.emit(OutputEvent(type="user_input", content="base-user"))
        await rec.emit(
            OutputEvent(
                type="assistant_image",
                payload={"url": "https://img.example/x.png", "detail": "low"},
            )
        )
        await rec.emit(OutputEvent(type="resume_batch", payload={"events": []}))
        await rec.emit(
            OutputEvent(type="tool_start", content="bash", payload={"job": "j1"})
        )
        # text → write_stream, processing hooks counted, activity recorded.
        assert "base-text" in rec.stream_text
        assert rec.processing_starts == 1 and rec.processing_ends == 1
        assert "tool_start" in rec.activity_types()

        # ── MultiOutputRouter — start/stop/write_to/flush cascade ──
        multi_default = OutputRecorder()
        chan_a = OutputRecorder()
        chan_b = OutputRecorder()
        multi = MultiOutputRouter(multi_default, outputs={"a": chan_a, "b": chan_b})
        await multi.start()
        assert multi_default.is_running is True
        assert chan_a.is_running is True and chan_b.is_running is True
        # write_to delivers to exactly the named module.
        await multi.write_to("a", "for-channel-a")
        assert chan_a.writes == ["for-channel-a"]
        assert chan_b.writes == []
        # Unknown target is a warned no-op, not a crash.
        await multi.write_to("nonexistent", "dropped")
        assert chan_b.writes == []
        await multi.flush()
        assert chan_a._flushed == 1 and chan_b._flushed == 1
        await multi.stop()
        assert chan_a.is_running is False and chan_b.is_running is False

        # ── OutputRouter interactive bus (Phase B) ──
        ibus_default = OutputRecorder()
        ibus = OutputRouter(default_output=ibus_default)
        await ibus.start()
        try:
            # submit_reply for an event nobody is awaiting → (False, unknown).
            accepted, status = ibus.submit_reply_with_status(
                UIReply(event_id="never-emitted", action_id="ok")
            )
            assert accepted is False and status == "unknown"

            # emit_and_wait requires interactive=True and a non-empty id.
            with pytest.raises(ValueError, match="interactive=True"):
                await ibus.emit_and_wait(OutputEvent(type="confirm"))
            with pytest.raises(ValueError, match="non-empty event.id"):
                await ibus.emit_and_wait(OutputEvent(type="confirm", interactive=True))

            # Happy path: a renderer replies before the await resolves.
            evt = OutputEvent(type="confirm", interactive=True, id="evt-1")

            async def _reply_later() -> None:
                await asyncio.sleep(0.02)
                ok = ibus.submit_reply(
                    UIReply(event_id="evt-1", action_id="accept", values={"v": 1})
                )
                assert ok is True

            reply_task = asyncio.create_task(_reply_later())
            reply = await ibus.emit_and_wait(evt)
            await reply_task
            assert reply.event_id == "evt-1"
            assert reply.action_id == "accept"
            assert reply.values == {"v": 1}
            assert reply.is_timeout is False and reply.is_superseded is False

            # Timeout path: nobody replies within the window → __timeout__.
            timed_out = await ibus.emit_and_wait(
                OutputEvent(type="confirm", interactive=True, id="evt-2"),
                timeout_s=0.05,
            )
            assert timed_out.is_timeout is True
            assert timed_out.action_id == "__timeout__"
        finally:
            await ibus.stop()

    async def test_user_command_runs_against_live_agent(self, make_agent):
        """user_command protocol — a real slash command runs against the
        live agent. The AGENT-layer command reads ``context.agent`` (the
        constructed :class:`Agent`) and a consumed command short-circuits
        the LLM entirely.

        Mirrors: ``bootstrap/agent_init.py._try_slash_command_text`` /
        ``_prepare_injected_input`` reached via ``Agent.inject_input``.
        """
        agent = make_agent(
            script=[
                ScriptEntry("LLM saw the reworded text", match="REWORDED"),
                ScriptEntry("LLM saw the unknown command", match="unknown-command"),
            ]
        )
        # Register our commands on the input module's dispatcher, exactly
        # as ``_init_user_commands`` wires the builtins.
        ping = PingCommand()
        reword = RewordCommand()
        context = UserCommandContext(
            agent=agent, session=getattr(agent, "session", None)
        )
        agent.input.set_user_commands({"ping": ping, "reword": reword}, context)

        await agent.start()
        try:
            llm_calls_before = agent.llm.call_count
            # A consumed slash command: handled pre-LLM, never reaches
            # the controller.
            await agent.inject_input("/ping hello-world")

            # The command consumed the input — the LLM never ran.
            assert agent.llm.call_count == llm_calls_before
            # No assistant turn was produced.
            assert agent.controller.conversation.get_last_assistant_message() is None
            # The command's output surfaced via the output router as a
            # command_result activity.
            results = agent._recorder.activities_of_type("command_result")
            assert len(results) == 1
            assert results[0].detail == "pong from modules_agent: hello-world"

            # The ALIAS ``/p`` resolves to the same PingCommand.
            await agent.inject_input("/p via-alias")
            assert agent.llm.call_count == llm_calls_before
            results = agent._recorder.activities_of_type("command_result")
            assert len(results) == 2
            assert results[1].detail == "pong from modules_agent: via-alias"

            # A command that returns an error surfaces as a command_error
            # activity and still does NOT reach the LLM.
            await agent.inject_input("/ping boom")
            assert agent.llm.call_count == llm_calls_before
            errors = agent._recorder.activities_of_type("command_error")
            assert len(errors) == 1
            assert errors[0].detail == "ping refused: 'boom' is not allowed"

            # A NON-consuming command rewrites the input; the rewritten
            # text flows through to the LLM as a real user turn.
            await agent.inject_input("/reword fix this please")
            assert agent.llm.call_count == llm_calls_before + 1
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
            assert "LLM saw the reworded text" in last.get_text_content()
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "REWORDED: fix this please" in convo_text

            # An unknown slash command falls through to the LLM as input.
            await agent.inject_input("/unknown-command still text")
            assert agent.llm.call_count == llm_calls_before + 2

            # ── Slash-to-skill fallback (input/base.py _dispatch_skill_slash) ──
            # A ``/<skill-name>`` that doesn't shadow a registered command
            # resolves against the agent's SkillRegistry and rewrites the
            # turn into a skill-invocation preamble.
            agent.skills.add(
                Skill(
                    name="deploy",
                    description="Deploy the service.",
                    body="STEP 1: smoke test.",
                    origin="user",
                )
            )
            # try_user_command on a non-slash string returns None directly.
            assert await agent.input.try_user_command("not a slash") is None
            # /deploy resolves to a non-consuming skill turn.
            skill_result = await agent.input.try_user_command("/deploy now")
            assert skill_result is not None
            assert skill_result.consumed is False
            assert "deploy" in skill_result.output.lower()
            # An unknown /name matches neither a command nor a skill → None.
            assert await agent.input.try_user_command("/totally-unknown x") is None
            # A disabled skill returns an error result (not a silent drop).
            agent.skills.disable("deploy")
            disabled_result = await agent.input.try_user_command("/deploy again")
            assert disabled_result is not None
            assert disabled_result.error is not None
            assert "disabled" in disabled_result.error
            agent.skills.enable("deploy")

            # is_running reflects the running input module.
            assert agent.input.is_running is True
        finally:
            await agent.stop()

    async def test_tool_executes_direct_mode_feeds_controller(self, make_agent):
        """tool protocol — a real DIRECT-mode tool is dispatched by the
        controller from a ``[/...]`` block, the executor runs it, and the
        result is fed back into the controller's conversation so the next
        LLM turn can see it.

        Mirrors: ``core/agent_handlers.py._dispatch_tool_event`` →
        ``core/executor.py.submit`` → result collection back into the
        controller loop.
        """
        agent = make_agent(
            script=[
                # Turn 1: single direct tool call → wrap-up.
                ScriptEntry(
                    "[/recorder]@@msg=direct-mode\n[recorder/]", match="use the tool"
                ),
                ScriptEntry("tool result received", match="recorder saw: direct-mode"),
                # Turn 2: TWO tool calls in one response → parallel dispatch.
                ScriptEntry(
                    "[/recorder]@@msg=first\n[recorder/]\n"
                    "[/recorder]@@msg=second\n[recorder/]",
                    match="run two tools",
                ),
                ScriptEntry("both tools done", match="recorder saw: second"),
                # Turn 3: a tool that returns an error result.
                ScriptEntry(
                    "[/recorder]@@msg=explode\n[recorder/]", match="trigger a failure"
                ),
                ScriptEntry("handled the failure", match="deliberate explosion"),
            ]
        )
        tool = RecordingTool()
        agent.registry.register_tool(tool)
        agent.executor.register_tool(tool)

        await agent.start()
        try:
            await agent._process_event(create_user_input_event("use the tool"))

            # The tool actually executed in DIRECT mode with the parsed args.
            assert tool.executed_with == [{"msg": "direct-mode"}]

            # The result was fed back: the controller's conversation now
            # contains the tool output, and the follow-up LLM turn ran.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "recorder saw: direct-mode" in convo_text
            last = agent.controller.conversation.get_last_assistant_message()
            assert last is not None
            assert "tool result received" in last.get_text_content()
            # Two LLM turns total: the tool-call turn and the wrap-up turn.
            assert agent.llm.call_count == 2

            # ── Parallel dispatch: two tool calls in ONE assistant turn ──
            await agent._process_event(create_user_input_event("run two tools"))
            # Both ran (executor dispatches them concurrently), in order.
            assert tool.executed_with == [
                {"msg": "direct-mode"},
                {"msg": "first"},
                {"msg": "second"},
            ]
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "recorder saw: first" in convo_text
            assert "recorder saw: second" in convo_text
            last = agent.controller.conversation.get_last_assistant_message()
            assert "both tools done" in last.get_text_content()

            # ── Error path: a tool returning ToolResult(error=...) ──
            await agent._process_event(create_user_input_event("trigger a failure"))
            assert tool.executed_with[-1] == {"msg": "explode"}
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The error text was fed back to the controller so the next
            # turn could react to it.
            assert "recorder failed: deliberate explosion" in convo_text
            last = agent.controller.conversation.get_last_assistant_message()
            assert "handled the failure" in last.get_text_content()
        finally:
            await agent.stop()
