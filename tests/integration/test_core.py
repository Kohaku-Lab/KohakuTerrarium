"""Integration suite for ``kohakuterrarium.core`` — the agent runtime.

Each test method here drives a *complete* feature workflow of the
``core/`` package end-to-end through a real :class:`Agent`, constructed
and hosted exactly the way the real consumers do it:

* :mod:`kohakuterrarium.bootstrap.agent_init` builds the agent from an
  ``AgentConfig`` (``Agent(config)`` runs the whole ``AgentInitMixin``
  chain — LLM, registry, executor, sub-agents, controller, I/O).
* :mod:`kohakuterrarium.terrarium.creature_host` wraps that live
  ``Agent`` in a :class:`Creature` and adds it to a real
  :class:`Terrarium` engine; ``Creature.chat`` is the canonical
  inject-input + drain-output cycle every HTTP / WS / CLI endpoint
  uses.

The ONLY seam is the LLM: both ``create_llm_provider`` import sites
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to a
:class:`ScriptedLLM`. Every other collaborator — controller loop,
executor, sub-agent manager, output router, compact manager,
termination checker, the Terrarium engine — is the real thing.

No shape asserts: every assertion pins an exact value or an observable
side effect (conversation contents, tool output text, engine state).
"""

import asyncio
from pathlib import Path

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.builtins.tools.stop_task import StopTaskTool
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.llm.message import FilePart, ImagePart, TextPart
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder

# ---------------------------------------------------------------------------
# Deterministic tool stubs — real BaseTool subclasses, no faked methods.
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    """DIRECT tool: returns its bracket body (the ``content`` arg) back.

    The bracket tool format maps the text between ``[/echo]`` and
    ``[echo/]`` onto the ``content`` argument.
    """

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the call body back."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output=f"echoed:{args.get('content', '')}")


class _SlowBackgroundTool(BaseTool):
    """BACKGROUND tool: yields after a short sleep so the controller
    must promote it and pick the result up on a later turn."""

    @property
    def tool_name(self) -> str:
        return "slowbg"

    @property
    def description(self) -> str:
        return "A background tool that completes after a short delay."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        await asyncio.sleep(0.05)
        return ToolResult(output="background-finished")


class _FailingTool(BaseTool):
    """DIRECT tool that always returns an error result.

    Drives the executor's error-result path and the
    ``_emit_direct_completion_activity`` / ``_format_text_results``
    error branches — distinct from a tool that *raises*.
    """

    @property
    def tool_name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails with an error result."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(error="deliberate failure", exit_code=2)


class _RaisingTool(BaseTool):
    """DIRECT tool whose ``_execute`` raises — exercises the executor's
    generic-exception arm (distinct from an error *result*)."""

    @property
    def tool_name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Raises a RuntimeError when executed."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        raise RuntimeError("kaboom")


class _MultimodalTool(BaseTool):
    """DIRECT tool returning a multimodal ToolResult (text + image).

    Drives ``tool_output.normalize_tool_output`` down its
    list-of-parts branch: the image part has no artifact store here so
    it is rendered as a safe text placeholder (raw base64 never reaches
    model context) and ``render_content_text`` joins the parts.
    """

    @property
    def tool_name(self) -> str:
        return "snap"

    @property
    def description(self) -> str:
        return "Returns a multimodal result: a caption plus an image."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(
            output=[
                TextPart(text="caption-for-the-snapshot"),
                ImagePart(url="data:image/png;base64,aGVsbG8td29ybGQ=", detail="low"),
            ]
        )


class _HugeOutputTool(BaseTool):
    """DIRECT tool returning text far over its ``max_output`` byte cap.

    Drives ``tool_output.truncate_text_utf8`` — the executor reads
    ``tool.config.max_output`` and the normalised output carries the
    truncation note + ``truncated`` metadata.
    """

    @property
    def tool_name(self) -> str:
        return "huge"

    @property
    def description(self) -> str:
        return "Returns a very large text blob."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="A" * 5000)


class _BlockingTool(BaseTool):
    """DIRECT tool that blocks forever — used to test interrupt()."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    @property
    def tool_name(self) -> str:
        return "block"

    @property
    def description(self) -> str:
        return "Blocks until cancelled."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.started.set()
        await asyncio.sleep(3600)
        return ToolResult(output="never")


class _BackgroundRaisingTool(BaseTool):
    """BACKGROUND tool whose ``_execute`` raises after a short delay.

    Drives the executor's ``_run_tool`` background-exception arm: the
    raised error is caught, recorded as an ERROR job, and the
    ``_on_complete`` callback fires a ``tool_complete`` event carrying
    the error string.
    """

    @property
    def tool_name(self) -> str:
        return "bgboom"

    @property
    def description(self) -> str:
        return "A background tool that raises after a short delay."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        await asyncio.sleep(0.03)
        raise RuntimeError("background-kaboom")


class _RewriteArgsPlugin(BasePlugin):
    """Real plugin: rewrites a tool call's args via ``pre_tool_dispatch``.

    The canonical cross-cutting-concern pattern — a plugin sits between
    the parser and the executor and mutates the ``ToolCallEvent``. Here
    it appends a marker to the ``echo`` tool's ``content`` arg so the
    test can observe the rewrite landed in the executed tool's output.
    """

    name = "rewrite-args"
    priority = 10

    async def pre_tool_dispatch(self, call, context):
        if call.name != "echo":
            return None
        new_args = dict(call.args)
        new_args["content"] = f"{new_args.get('content', '')}-rewritten"
        return ToolCallEvent(name=call.name, args=new_args, raw=call.raw)


class _VetoPlugin(BasePlugin):
    """Real plugin: vetoes a named tool via ``PluginBlockError``."""

    name = "veto"
    priority = 20

    def __init__(self, blocked_tool: str) -> None:
        super().__init__()
        self._blocked = blocked_tool

    async def pre_tool_dispatch(self, call, context):
        if call.name == self._blocked:
            raise PluginBlockError(f"{call.name} is not allowed here")
        return None


class _RewriteResponsePlugin(BasePlugin):
    """Real plugin: rewrites the assistant text via ``post_llm_call``.

    Drives ``controller_plugins.run_post_llm_call_chain`` — when a
    plugin returns a changed string, the controller mutates the stored
    assistant message in place and emits an ``assistant_message_edited``
    activity. Here it appends a fixed suffix so the mutation is
    observable in the conversation.
    """

    name = "rewrite-response"
    priority = 30

    async def post_llm_call(self, messages, response_text, usage, **kwargs):
        if not response_text:
            return None
        return f"{response_text} [reviewed]"


class _SubagentGatePlugin(BasePlugin):
    """Real plugin: rewrites a sub-agent task via ``pre_subagent_run``,
    and vetoes one named sub-agent entirely.

    Exercises ``run_pre_subagent_dispatch`` — both the task-rewrite arm
    (returns a new task string) and the ``PluginBlockError`` veto arm
    that synthesises a blocked sub-agent result back to the controller.
    """

    name = "subagent-gate"
    priority = 15

    def __init__(self, blocked_name: str) -> None:
        super().__init__()
        self._blocked = blocked_name

    async def pre_subagent_run(self, task, **kwargs):
        if kwargs.get("name") == self._blocked:
            raise PluginBlockError(f"{self._blocked} is forbidden")
        return f"{task} [gated]"


class _OneShotTrigger(BaseTrigger):
    """A real BaseTrigger that fires exactly one event then idles.

    Drives the unified ``TriggerEvent`` model end-to-end through the
    real ``TriggerManager._run_loop`` — the same path ``TimerTrigger``
    and ``ChannelTrigger`` take. Exposes ``fired`` so the test can wait
    for the trigger loop to actually emit before asserting.
    """

    def __init__(self, prompt: str) -> None:
        super().__init__(prompt=prompt)
        self._gate = asyncio.Event()
        self._done = False
        self.fired = asyncio.Event()

    async def wait_for_trigger(self) -> TriggerEvent | None:
        if self._done or not self._running:
            # Block until stopped so the run loop doesn't busy-spin.
            await self._gate.wait()
            return None
        self._done = True
        self.fired.set()
        return self._create_event(
            EventType.TIMER,
            content=self.prompt,
            context={"trigger": "oneshot"},
        )

    async def _on_stop(self) -> None:
        self._gate.set()


# ---------------------------------------------------------------------------
# Fixtures — mirror bootstrap/agent_init + terrarium/creature_host.
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites to a ScriptedLLM.

    ``bootstrap.agent_init`` imports the symbol directly and
    ``bootstrap.llm`` defines it — patching only one leaves a real
    provider on the other path. The closure lets a test set its script
    before it builds the agent.
    """

    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


@pytest.fixture
def make_creature(scripted_llm, tmp_path):
    """Build a real ``Agent`` and wrap it in a ``Creature``.

    This is the ``terrarium/creature_host.build_creature`` path with an
    in-memory ``AgentConfig`` — the same construction the engine uses
    for a solo ``kt run`` creature. Returns ``(creature, recorder)``;
    the recorder is swapped onto the router's default output for
    behaviour assertions.
    """

    def _build(
        *,
        script=None,
        system_prompt="You are a test agent.",
        tools=None,
        ephemeral=False,
        max_iterations=None,
        termination=None,
        compact=None,
        creature_id="solo",
    ):
        if script is not None:
            scripted_llm["script"] = script
        cfg = AgentConfig(
            name=creature_id,
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
            termination=termination,
            compact=compact,
        )
        agent = Agent(cfg)
        recorder = OutputRecorder()
        agent.output_router.default_output = recorder
        creature = Creature(creature_id=creature_id, name=creature_id, agent=agent)
        return creature, recorder

    return _build


async def _drain_chat(creature: Creature, message: str) -> str:
    """``Creature.chat`` consumed to completion — the canonical drive."""
    chunks: list[str] = []
    async for chunk in creature.chat(message):
        chunks.append(chunk)
    return "".join(chunks)


def _assistant_text(agent: Agent) -> str:
    last = agent.controller.conversation.get_last_assistant_message()
    assert last is not None, "expected an assistant message in conversation"
    return last.get_text_content()


# ---------------------------------------------------------------------------
# The integration suite.
# ---------------------------------------------------------------------------


class TestCoreIntegration:
    """Each method runs one complete ``core/`` feature workflow."""

    async def test_full_turn_cycle_with_direct_and_background_tools(
        self, make_creature
    ):
        """Engine-hosted creature: input -> controller loop -> DIRECT tool
        dispatch + result feedback -> a second turn dispatches a
        BACKGROUND tool that completes and routes its result back via
        the unified TriggerEvent model -> output streamed to chat.

        Mirrors: ``creature_host.Creature.chat`` driving a real engine
        creature, the way every HTTP/WS chat endpoint does.
        """
        creature, recorder = make_creature(
            script=[
                # Turn 1, round 1: call the direct echo tool.
                ScriptEntry("[/echo]ping[echo/]", match="hello"),
                # Turn 1, round 2: tool result fed back, wrap up.
                ScriptEntry("direct done", match="echoed:ping"),
                # Turn 2: dispatch the BACKGROUND tool. A background tool
                # is promoted immediately (not awaited) so the turn ends
                # right after this single round; the surrounding text
                # streams out as the turn's reply.
                ScriptEntry("kicked off bg work [/slowbg][slowbg/]", match="second"),
                # The background completion arrives later as its own
                # tool_complete TriggerEvent, driving a fresh turn.
                ScriptEntry("background acknowledged", match="background-finished"),
            ]
        )
        agent = creature.agent
        echo, slowbg = _EchoTool(), _SlowBackgroundTool()
        fail_tool, boom_tool = _FailingTool(), _RaisingTool()
        snap_tool = _MultimodalTool()
        # A tiny 100-byte cap so the 5000-char blob is truncated.
        huge_tool = _HugeOutputTool(ToolConfig(max_output=100))
        for tool in (echo, slowbg, fail_tool, boom_tool, snap_tool, huge_tool):
            agent.registry.register_tool(tool)
            agent.executor.register_tool(tool)
        # Register a real plugin whose ``pre_tool_dispatch`` rewrites the
        # echo call's args — the cross-cutting-concern extension point.
        agent.plugins.register(_RewriteArgsPlugin())

        async with Terrarium() as engine:
            added = await engine.add_creature(creature)
            # The engine placed the creature in a fresh singleton graph
            # and started it.
            assert engine.get_creature("solo") is creature
            assert added.graph_id != ""
            assert creature.is_running is True

            # --- Turn 1: direct tool round-trip -------------------------
            out1 = await _drain_chat(creature, "hello, run echo")
            # The final assistant text of turn 1 streamed to the chat pipe.
            assert "direct done" in out1
            # The echo tool actually ran: its output is in the conversation.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "echoed:ping" in convo_text
            # The plugin's pre_tool_dispatch rewrite landed: the executed
            # tool saw the appended marker, so its output carries it.
            assert "echoed:ping-rewritten" in convo_text
            # The controller looped exactly twice for turn 1 (call, wrap-up).
            assert agent.llm.call_count == 2

            # --- Turn 2 + 3: background tool promote + completion -------
            # A BACKGROUND tool is promoted immediately, so turn 2 is a
            # single controller round: the surrounding text streams out
            # and the turn ends without awaiting the tool.
            out2 = await _drain_chat(creature, "second request please")
            assert "kicked off bg work" in out2

            # The background job completes on its own; the executor's
            # completion callback injects a ``tool_complete`` TriggerEvent
            # which drives exactly one more controller turn whose final
            # assistant text acknowledges the real background output.
            for _ in range(100):
                if "background acknowledged" in _assistant_text(agent):
                    break
                await asyncio.sleep(0.02)
            assert "background acknowledged" in _assistant_text(agent)
            bg_convo = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The real background output ("background-finished") reached
            # the conversation as a tool-result message.
            assert "background-finished" in bg_convo

            # Regression guard for B-fat2-core-1 (FIXED): a tool declared
            # ``ExecutionMode.BACKGROUND`` is submitted to the executor as
            # background, so the executor's own ``_on_complete`` callback
            # delivers the completion. The ``backgroundify`` handle must
            # NOT also fire ``_on_backgroundify_complete`` — that double
            # completion ran the controller one EXTRA turn. The fix passes
            # ``on_bg_complete=None`` to the handle whenever the executor
            # already delivers the completion. Expected: exactly 4
            # controller calls (turn1 ×2 + turn2 ×1 + one bg follow-up).
            for _ in range(100):
                if agent.llm.call_count >= 4:
                    break
                await asyncio.sleep(0.02)
            # Settle: on the unfixed (double-fire) code a 5th controller
            # call would be scheduled right here by the second completion.
            await asyncio.sleep(0.3)
            assert agent.llm.call_count == 4

            # Re-bind a fresh scripted LLM for the remaining turns so the
            # ``match``-keyed entries below resolve order-independently.
            rest_llm = ScriptedLLM(
                [
                    # Turn 4: a veto plugin blocks the echo tool.
                    ScriptEntry("[/echo]blocked attempt[echo/]", match="echo again"),
                    # Turn 4b: the synthesised block result rides the next
                    # input — the controller sees it and acknowledges.
                    ScriptEntry("noticed the block", match="is not allowed here"),
                    # Turn 5: TWO direct tools in one round — parallel exec.
                    ScriptEntry(
                        "[/echo]left[echo/] and [/echo]right[echo/]",
                        match="run two echoes",
                    ),
                    ScriptEntry("both echoes done", match="echoed:left"),
                    # Turn 6: an ERROR-result tool and a RAISING tool.
                    ScriptEntry(
                        "[/fail][fail/] then [/boom][boom/]",
                        match="trigger failures",
                    ),
                    ScriptEntry("saw the failures", match="deliberate failure"),
                    # Turn 7: a multimodal-output tool + a huge-output
                    # tool — exercises tool_output normalization.
                    ScriptEntry(
                        "[/snap][snap/] and [/huge][huge/]",
                        match="snapshot and blob",
                    ),
                    ScriptEntry("processed media", match="caption-for-the-snapshot"),
                    # Turn 8: framework commands — ##jobs## lists jobs,
                    # ##info## fetches a tool's docs, ##read_job## on a
                    # ghost id errors, ##info## on an unknown tool errors.
                    # All resolve inline in text mode via the controller's
                    # command handler; output is spliced into the
                    # assistant message within the SAME round.
                    ScriptEntry(
                        "checking [/jobs][jobs/] and [/info]echo[info/] and "
                        "[/read_job]ghost_job_id[read_job/] and "
                        "[/info]no_such_tool[info/]",
                        match="run the commands",
                    ),
                    # Turn 9: a custom output-handler check.
                    ScriptEntry("ephemeral reply", match="ephemeral check"),
                ]
            )
            agent.llm = rest_llm
            agent.controller.llm = rest_llm

            # --- Programmatic introspection API -------------------------
            # ``get_state`` is the monitoring snapshot the TUI/API poll.
            state = agent.get_state()
            assert state["name"] == "solo"
            assert state["running"] is True
            assert "echo" in state["tools"]
            assert "slowbg" in state["tools"]
            # message_count matches the live conversation length.
            assert state["message_count"] == len(
                agent.controller.conversation.get_messages()
            )
            # ``tools`` / ``conversation_history`` properties agree with
            # the registry and the controller's conversation. The engine
            # also registers its own channel tools, so the two we added
            # must be a subset.
            assert {"echo", "slowbg"}.issubset(set(agent.tools))
            assert agent.conversation_history == (
                agent.controller.conversation.to_messages()
            )

            # --- System-prompt hot-edit ---------------------------------
            base_prompt = agent.get_system_prompt()
            assert "You are a test agent." in base_prompt
            agent.update_system_prompt("Extra runtime guideline.")
            assert "Extra runtime guideline." in agent.get_system_prompt()
            # The original text survives an append.
            assert "You are a test agent." in agent.get_system_prompt()
            agent.update_system_prompt("Replaced entirely.", replace=True)
            assert agent.get_system_prompt() == "Replaced entirely."

            # --- session_info snapshot ----------------------------------
            # No session store attached → the ``own`` view is an empty
            # token dict, the ``all_loops`` view an empty list.
            info = agent.session_info()
            assert info["agent"] == "solo"
            assert info["tokens"] == {}
            assert agent.session_info(tokens_view="all_loops")["tokens"] == []

            # --- Turn 4: a veto plugin blocks the echo tool -------------
            # Register a second plugin whose pre_tool_dispatch raises
            # PluginBlockError for ``echo``. The call never reaches the
            # executor; the block message is synthesised as a queued
            # ``tool_complete`` event so the controller sees it on its
            # next round. Turn 4's own round produces no plain text (it
            # was a pure tool call), so ``out4`` is empty — the veto
            # short-circuits before any feedback loop continues.
            agent.plugins.register(_VetoPlugin("echo"))
            out4 = await _drain_chat(creature, "try echo again")
            assert out4 == ""  # pure tool-call round, then vetoed
            # The echo tool was NEVER executed for turn 4: the veto fired
            # before the executor, so no new echo job appears.
            echo_jobs = [
                j
                for j in agent.executor.job_store.get_all_statuses()
                if "echo" in j.job_id
            ]
            assert len(echo_jobs) == 1  # only turn 1's echo ever ran
            # The synthesised block result was queued on the controller.
            assert agent.controller.has_pending_events() is True
            # Turn 4b: the next input batches with the queued block
            # result; the controller acknowledges the veto message.
            out4b = await _drain_chat(creature, "is not allowed here?")
            assert "noticed the block" in out4b
            # rest_llm: turn-4 round (1) + turn-4b round (1) = 2.
            assert rest_llm.call_count == 2

            # --- Turn 5: two DIRECT tools in one round, run in parallel -
            # Disable the veto plugin so this turn's echoes actually run;
            # ``disable`` drops it from the active set without unregister.
            assert agent.plugins.disable("veto") is True
            assert agent.plugins.is_enabled("veto") is False
            calls_before_parallel = agent.llm.call_count
            out5 = await _drain_chat(creature, "run two echoes")
            assert "both echoes done" in out5
            convo5 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # Both parallel echoes executed — the rewrite plugin still
            # appended its marker to each.
            assert "echoed:left-rewritten" in convo5
            assert "echoed:right-rewritten" in convo5
            # Two echo jobs ran this turn; turn 1's was the only prior one.
            echo_jobs_after = [
                j
                for j in agent.executor.job_store.get_all_statuses()
                if "echo" in j.job_id
            ]
            assert len(echo_jobs_after) == 3
            assert agent.llm.call_count == calls_before_parallel + 2

            # --- Turn 6: an error-RESULT tool and a RAISING tool --------
            # The executor records the error result with its exit code,
            # and converts the raised RuntimeError into an error job too.
            out6 = await _drain_chat(creature, "trigger failures now")
            assert "saw the failures" in out6
            # Both failure modes surfaced as tool_error activities.
            errs = recorder.activities_of_type("tool_error")
            assert len(errs) >= 2
            convo6 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The error-result tool's message and the raised exception
            # text both reached the conversation as feedback.
            assert "deliberate failure" in convo6
            assert "kaboom" in convo6
            # The executor's job_store recorded the failing tool's exact
            # exit code and an ERROR state for the raising tool.
            fail_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("fail_")
            )
            assert fail_status.state.value == "error"
            boom_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("boom_")
            )
            assert boom_status.state.value == "error"

            # --- Turn 7: multimodal + huge tool output normalization ----
            # ``snap`` returns a text+image ToolResult; with no artifact
            # store the image is rendered as a safe placeholder (no raw
            # base64 in context). ``huge`` returns 5000 chars but its
            # ToolConfig caps output at 100 bytes → truncated.
            out7 = await _drain_chat(creature, "snapshot and blob now")
            assert "processed media" in out7
            convo7 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The multimodal tool's caption reached context as text.
            assert "caption-for-the-snapshot" in convo7
            # The raw base64 image data was NOT carried into context —
            # only a safe data-URL placeholder.
            assert "aGVsbG8td29ybGQ=" not in convo7
            assert "elided" in convo7
            # The huge tool's output was byte-capped — the truncation
            # note landed and the full 5000-char blob did not.
            assert "truncated to 100 bytes" in convo7
            assert "A" * 5000 not in convo7
            # The executor's job_store recorded the truncation metadata.
            huge_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("huge_")
            )
            assert huge_status.state.value == "done"

            # --- Turn 8: framework commands resolve inline --------------
            # ``[/jobs]`` / ``[/info]`` / ``[/read_job]`` are framework
            # commands, not tools — the controller's
            # ``_execute_command_inline`` runs them inside the SAME round
            # and splices the result (or error) into the stored
            # assistant message. ``info echo`` returns docs; ``read_job``
            # on a ghost id and ``info no_such_tool`` both error.
            out8 = await _drain_chat(creature, "run the commands please")
            # The surrounding scripted text streamed.
            assert "checking" in out8
            convo8 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            # The ##info echo## command spliced echo's description into
            # the stored assistant message verbatim.
            assert "Echo the call body back." in convo8
            # The erroring commands spliced a "[Command Error: ...]"
            # marker into the assistant message.
            assert "Command Error" in convo8
            # A command_done activity was surfaced for the good commands
            # and a command_error for the failing ones.
            cmd_done = recorder.activities_of_type("command_done")
            assert len(cmd_done) >= 2
            cmd_err = recorder.activities_of_type("command_error")
            assert len(cmd_err) >= 2

            # --- set_output_handler: a custom secondary sink -----------
            # ``set_output_handler`` wraps a plain callback into an
            # OutputModule and adds it as a secondary output. The next
            # turn's streamed text must reach the callback verbatim.
            captured: list[str] = []
            agent.set_output_handler(captured.append)
            out_cb = await _drain_chat(creature, "ephemeral check please")
            assert "ephemeral reply" in out_cb
            assert "ephemeral reply" in "".join(captured)

        # Engine __aexit__ stopped the creature.
        assert creature.is_running is False

        # --- A separate EPHEMERAL creature: conversation resets per turn.
        # ``ephemeral=True`` flips ``ControllerConfig.ephemeral`` so the
        # controller flushes everything but the system prompt after each
        # interaction (the group-chat-bot path).
        eph_creature, _ = make_creature(
            script=[
                ScriptEntry("first ephemeral answer", match="alpha"),
                ScriptEntry("second ephemeral answer", match="beta"),
            ],
            ephemeral=True,
            creature_id="eph",
        )
        eph_agent = eph_creature.agent
        assert eph_agent.controller.is_ephemeral is True
        async with Terrarium() as engine2:
            await engine2.add_creature(eph_creature)
            eph_out1 = await _drain_chat(eph_creature, "alpha question")
            assert "first ephemeral answer" in eph_out1
            # After an ephemeral turn only the system prompt survives.
            roles_after_1 = [
                m.role for m in eph_agent.controller.conversation.get_messages()
            ]
            assert roles_after_1 == ["system"]
            eph_out2 = await _drain_chat(eph_creature, "beta question")
            # Second turn ran fresh — its reply keyed on "beta" (the
            # match would have failed if stale "alpha" context leaked).
            assert "second ephemeral answer" in eph_out2
            roles_after_2 = [
                m.role for m in eph_agent.controller.conversation.get_messages()
            ]
            assert roles_after_2 == ["system"]
        assert eph_creature.is_running is False

    async def test_subagent_dispatch_and_result_routing(self, make_creature):
        """Controller dispatches a registered sub-agent; the sub-agent
        runs its own LLM loop; its result routes back into the parent
        conversation and drives a follow-up turn.

        Mirrors: a creature with a registered sub-agent (the VERTICAL
        composition level) — ``bootstrap.subagents`` wires these, and
        the controller loop dispatches them via ``_dispatch_subagent_event``.
        """
        creature, recorder = make_creature(
            script=[
                # Parent round 1: delegate to the explore sub-agent.
                ScriptEntry(
                    "[/explore]survey the repo[explore/]",
                    match="investigate",
                ),
                # Parent round 2: sub-agent result fed back, summarise.
                ScriptEntry("summary: exploration complete", match="explored:done"),
                # Turn 2: delegate to a SECOND sub-agent (multi-turn).
                ScriptEntry("[/planner]draft a plan[planner/]", match="now plan"),
                ScriptEntry("summary: plan received", match="planned:ready"),
                # Turn 3: a gate plugin rewrites the explore task — the
                # spawned sub-agent's task carries the "[gated]" suffix.
                ScriptEntry("[/explore]check again[explore/]", match="gated rerun"),
                ScriptEntry("summary: gated run complete", match="explored:gated"),
                # Turn 4: dispatch the FORBIDDEN sub-agent — the gate
                # plugin vetoes it; the synthesised block result is
                # queued and rides the next input.
                ScriptEntry("[/planner]nope[planner/]", match="forbidden delegate"),
                ScriptEntry("noticed the veto", match="is forbidden"),
            ]
        )
        agent = creature.agent
        # Register a sub-agent the same way ``bootstrap.subagents.init_subagents``
        # does: into the SubAgentManager (so it can be spawned) AND into
        # the Registry (so the controller's stream parser recognises the
        # ``[/explore]`` tag).
        sa_cfg = SubAgentConfig(
            name="explore",
            description="Survey a codebase.",
            tools=[],
            system_prompt="You are an explorer.",
            max_turns=1,
        )
        agent.subagent_manager.register(sa_cfg)
        agent.registry.register_subagent("explore", sa_cfg)
        # A second sub-agent — its own multi-turn config — proving the
        # VERTICAL composition level scales past one delegate.
        plan_cfg = SubAgentConfig(
            name="planner",
            description="Draft an execution plan.",
            tools=[],
            system_prompt="You are a planner.",
            max_turns=2,
        )
        agent.subagent_manager.register(plan_cfg)
        agent.registry.register_subagent("planner", plan_cfg)
        # The sub-agents share one deterministic LLM; each spawn consumes
        # the next scripted reply in order. The gate plugin appends
        # " [gated]" to every task — the explore sub-agent's third spawn
        # receives a task ending in "[gated]", so its scripted reply is
        # keyed on that exact substring.
        agent.subagent_manager.llm = ScriptedLLM(
            [
                "explored:done",
                "planned:ready",
                ScriptEntry("explored:gated ok", match="[gated]"),
            ]
        )

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            # Both registered sub-agents are visible on the property.
            assert set(agent.subagents) == {"explore", "planner"}

            out = await _drain_chat(creature, "investigate the project")

            # The parent's final turn summarised the sub-agent's work.
            assert "summary: exploration complete" in out
            # The parent looped twice: dispatch, then summarise.
            assert agent.llm.call_count == 2
            # The sub-agent's result text reached the parent conversation
            # as a tool-result message.
            convo_text = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "explored:done" in convo_text
            # A subagent_done activity was emitted to the output router.
            done = recorder.activities_of_type("subagent_done")
            assert len(done) == 1

            # --- Turn 2: dispatch the SECOND sub-agent ------------------
            out2 = await _drain_chat(creature, "now plan the work")
            assert "summary: plan received" in out2
            # The planner sub-agent's own LLM output routed back.
            convo_text2 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "planned:ready" in convo_text2
            # Two distinct sub-agent runs → two subagent_done activities.
            assert len(recorder.activities_of_type("subagent_done")) == 2
            # Parent looped twice more (dispatch + summarise).
            assert agent.llm.call_count == 4

            # --- Turn 3: pre_subagent_run rewrites the task -------------
            # The gate plugin appends "[gated]" to the dispatched task.
            # The explore sub-agent's scripted reply is keyed on that
            # exact suffix, so a green turn here PROVES the rewrite
            # reached ``spawn_from_event``.
            agent.plugins.register(_SubagentGatePlugin("planner"))
            out3 = await _drain_chat(creature, "gated rerun please")
            assert "summary: gated run complete" in out3
            convo_text3 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "explored:gated ok" in convo_text3
            assert agent.llm.call_count == 6

            # --- Turn 4: pre_subagent_run VETOES a forbidden delegate --
            # The gate plugin raises PluginBlockError for "planner";
            # ``run_pre_subagent_dispatch`` synthesises a blocked result
            # ("planner is forbidden") queued on the controller. Turn 4's
            # own round is a pure sub-agent call → no plain text out.
            calls_before_veto = agent.llm.call_count
            out4 = await _drain_chat(creature, "forbidden delegate now")
            assert out4 == ""
            # The synthesised block result was queued.
            assert agent.controller.has_pending_events() is True
            # Turn 4b: the next input batches with the queued veto
            # result; the controller acknowledges it.
            out4b = await _drain_chat(creature, "is forbidden, ok?")
            assert "noticed the veto" in out4b
            convo_text4 = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "planner is forbidden" in convo_text4
            # Turn 4 round (1) + turn 4b round (1) = 2 more parent calls.
            assert agent.llm.call_count == calls_before_veto + 2

    async def test_compaction_and_termination(self, make_creature):
        """One workflow exercising context compaction AND a termination
        condition: a turn pushes token usage past the compact threshold,
        the compact manager splices the conversation, and on the next
        turn a termination keyword stops the agent.

        Mirrors: long-running creature behaviour — ``Agent.start`` builds
        the compact manager and termination checker; the controller loop
        fires ``_maybe_trigger_compact`` and ``_check_termination`` every
        iteration.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("answer one", match="one"),
                ScriptEntry("answer two", match="two"),
                ScriptEntry("answer three", match="three"),
                ScriptEntry("answer four", match="four"),
                ScriptEntry("ALL DONE now", match="five"),
            ],
            # Tiny budget so a few hundred tokens trips the threshold.
            compact={
                "max_tokens": 100,
                "threshold": 0.5,
                "target": 0.2,
                "keep_recent_turns": 1,
                "cooldown_seconds": 0.0,
            },
            termination={"keywords": ["ALL DONE"], "max_turns": 10},
        )
        agent = creature.agent

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            # Compact manager + termination checker were built by start().
            assert agent.compact_manager.config.max_tokens == 100
            assert agent.compact_manager.config.threshold == 0.5
            assert agent._termination_checker.is_active is True

            # Run several turns to build a conversation long enough for
            # the compact manager to have something to summarize.
            for word in ("one", "two", "three", "four"):
                await _drain_chat(creature, f"turn {word}")
            msgs_before = len(agent.controller.conversation.get_messages())
            assert msgs_before >= 8
            assert agent.is_running is True
            assert "answer four" in _assistant_text(agent)

            # Force the controller's recorded usage above the 50-token
            # compact threshold, then fire the turn-end hook.
            agent.controller._last_usage = {"prompt_tokens": 5000}
            compacts_before = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            # The compact job runs as a background task — let it finish.
            for _ in range(100):
                if agent.compact_manager._compact_count > compacts_before:
                    break
                await asyncio.sleep(0.02)
            assert agent.compact_manager._compact_count == compacts_before + 1
            # Compaction spliced the conversation: a summary message
            # replaced the older turns, so the message count dropped.
            msgs_after = len(agent.controller.conversation.get_messages())
            assert msgs_after < msgs_before

            # A second trigger right after a successful compaction with
            # usage still high fires another compaction (cooldown is 0s),
            # proving the compact path is re-entrant within a session.
            agent.controller._last_usage = {"prompt_tokens": 5000}
            count_after_first = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            for _ in range(100):
                if agent.compact_manager._compact_count > count_after_first:
                    break
                await asyncio.sleep(0.02)
            assert agent.compact_manager._compact_count == count_after_first + 1

            # With usage BELOW the threshold, the turn-end hook is a
            # no-op — the compact count stays put.
            agent.controller._last_usage = {"prompt_tokens": 10}
            steady = agent.compact_manager._compact_count
            agent._maybe_trigger_compact(agent.controller)
            await asyncio.sleep(0.05)
            assert agent.compact_manager._compact_count == steady

            # Final turn: assistant emits the termination keyword -> the
            # checker stops the agent inside the controller loop.
            await _drain_chat(creature, "turn five")
            assert "ALL DONE" in _assistant_text(agent)
            assert agent.is_running is False
            assert "ALL DONE" in (agent._termination_checker.reason or "")
            # After termination the engine reports the creature stopped.
            assert creature.is_running is False

        # --- A second creature: max_turns termination -------------------
        # ``max_turns`` is the simplest termination condition — the
        # checker counts controller turns and stops the agent once the
        # cap is hit. Each ``_drain_chat`` is one user turn; with a cap
        # of 2 the agent is still running after turn 1 and stopped after
        # turn 2.
        mt_creature, _ = make_creature(
            script=[
                ScriptEntry("turn-one reply", match="first"),
                ScriptEntry("turn-two reply", match="second"),
            ],
            termination={"max_turns": 2},
            creature_id="maxturns",
        )
        mt_agent = mt_creature.agent
        async with Terrarium() as engine_mt:
            await engine_mt.add_creature(mt_creature)
            assert mt_agent._termination_checker.config.max_turns == 2
            await _drain_chat(mt_creature, "first request")
            # One turn recorded — below the cap, still alive.
            assert mt_agent._termination_checker.turn_count == 1
            assert mt_agent.is_running is True
            await _drain_chat(mt_creature, "second request")
            # Second turn hit the cap — the checker terminated the agent.
            assert mt_agent.is_running is False
            assert "Max turns" in (mt_agent._termination_checker.reason or "")
        assert mt_creature.is_running is False

        # --- A third creature: max_iterations → IterationBudget ---------
        # ``max_iterations`` builds an ``IterationBudget`` consumed once
        # per controller turn. When it drains, ``_check_termination``
        # translates ``BudgetExhausted`` into a clean stop with the
        # "Iteration budget exhausted" reason.
        budget_creature, _ = make_creature(
            script=[
                ScriptEntry("budget turn one", match="alpha"),
                ScriptEntry("budget turn two", match="beta"),
            ],
            max_iterations=1,
            creature_id="budgeted",
        )
        b_agent = budget_creature.agent
        async with Terrarium() as engine_b:
            await engine_b.add_creature(budget_creature)
            assert b_agent.iteration_budget is not None
            assert b_agent.iteration_budget.total == 1
            # Turn 1 consumes the single budgeted iteration.
            await _drain_chat(budget_creature, "alpha request")
            assert b_agent.iteration_budget.exhausted is True
            # Turn 2's _check_termination over-consumes the drained
            # budget → BudgetExhausted → the run loop exits and the
            # agent is stopped.
            await _drain_chat(budget_creature, "beta request")
            assert b_agent.is_running is False
        assert budget_creature.is_running is False

    async def test_interrupt_mid_turn(self, make_creature):
        """A turn dispatches a blocking DIRECT tool; ``interrupt()`` is
        called while the controller is waiting on it; the processing
        task is cancelled cleanly and the agent stays alive for the
        next input.

        Mirrors: the TUI Escape key / web stop button — both call
        ``Agent.interrupt`` on a live engine creature.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("[/block][block/]", match="start blocking"),
                # After the interrupt, a fresh input runs normally.
                ScriptEntry("recovered fine", match="are you ok"),
                # A turn that dispatches the blocking tool again, this
                # time so the test can cancel that one job by id while
                # the rest of the turn keeps going.
                ScriptEntry("[/block][block/]", match="block for cancel"),
                ScriptEntry("job was cancelled", match="User manually interrupted"),
                # A turn calling stop_task on a non-existent job id.
                ScriptEntry(
                    "[/stop_task]@@job_id=ghost_999\n[stop_task/]",
                    match="cancel a ghost",
                ),
                ScriptEntry("ghost not found", match="Task not found"),
                # A turn dispatching a BACKGROUND tool that raises.
                ScriptEntry(
                    "kicked off failing bg [/bgboom][bgboom/]", match="fail bg"
                ),
                ScriptEntry("noticed bg failure", match="background-kaboom"),
            ]
        )
        agent = creature.agent
        block = _BlockingTool()
        stop_task = StopTaskTool()
        bgboom = _BackgroundRaisingTool()
        for tool in (block, stop_task, bgboom):
            agent.registry.register_tool(tool)
            agent.executor.register_tool(tool)

        async with Terrarium() as engine:
            await engine.add_creature(creature)

            # Drive a turn that will hang on the blocking tool.
            inject_task = asyncio.create_task(
                agent.inject_input("start blocking", source="chat")
            )
            # Wait until the blocking tool is actually executing.
            await asyncio.wait_for(block.started.wait(), timeout=5.0)

            # Interrupt: cancels the processing task + the direct job.
            agent.interrupt()
            # inject_input awaits the processing cycle; the cancellation
            # is caught inside ``_process_event_with_controller`` so
            # inject_input itself returns without raising.
            await asyncio.wait_for(inject_task, timeout=5.0)

            # The agent survived the interrupt and is still running.
            assert agent.is_running is True
            assert agent._processing_task is None
            # An interrupt activity was surfaced to the output router.
            assert len(recorder.activities_of_type("interrupt")) >= 1
            # Only one LLM call happened — the blocked turn never looped.
            assert agent.llm.call_count == 1

            # The creature accepts a fresh turn after the interrupt.
            out = await _drain_chat(creature, "are you ok")
            assert "recovered fine" in out
            assert agent.llm.call_count == 2

            # --- _cancel_job: cancel one running job by id -------------
            # ``_cancel_job`` is the TUI running-panel click handler. It
            # cancels a tracked direct job and emits a ``job_cancelled``
            # activity. Drive a turn that hangs on the blocking tool,
            # then cancel that exact job by id mid-flight.
            block.started.clear()
            cancel_task = asyncio.create_task(
                agent.inject_input("block for cancel now", source="chat")
            )
            await asyncio.wait_for(block.started.wait(), timeout=5.0)
            # Exactly one direct job is tracked — the blocking tool's.
            tracked = list(agent._active_handles.keys())
            assert len(tracked) == 1
            job_id = tracked[0]
            agent._cancel_job(job_id, "block")
            # Cancelling the only job lets the turn's feedback loop see
            # an interrupted result and drive one follow-up turn whose
            # script entry is keyed on the interruption message.
            await asyncio.wait_for(cancel_task, timeout=5.0)
            assert "job was cancelled" in _assistant_text(agent)
            # A job_cancelled activity was surfaced.
            assert len(recorder.activities_of_type("job_cancelled")) >= 1
            assert agent.is_running is True

            # --- stop_task builtin on a non-existent job ---------------
            # The ``stop_task`` builtin tool routes through the agent's
            # executor / sub-agent manager. A ghost id resolves to
            # nothing → an error result that the controller sees.
            out_ghost = await _drain_chat(creature, "cancel a ghost job")
            assert "ghost not found" in out_ghost
            ghost_convo = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "Task not found" in ghost_convo

            # --- a BACKGROUND tool that raises -------------------------
            # ``bgboom`` is promoted on dispatch; its ``_execute`` raises
            # after a short delay. The executor's ``_run_tool`` catches
            # the exception, records an ERROR job, and the completion
            # event carries the error string — which drives a follow-up
            # turn keyed on it.
            out_bg = await _drain_chat(creature, "fail bg now")
            assert "kicked off failing bg" in out_bg
            for _ in range(100):
                if "noticed bg failure" in _assistant_text(agent):
                    break
                await asyncio.sleep(0.02)
            assert "noticed bg failure" in _assistant_text(agent)
            # The executor recorded an ERROR job for the raising bg tool.
            bgboom_status = next(
                j
                for j in agent.executor.job_store.get_all_statuses()
                if j.job_id.startswith("bgboom_")
            )
            assert bgboom_status.state.value == "error"
            # ``executor.wait_all`` drains every tracked task and returns
            # the completed JobResults — the bg job's result carries its
            # error string.
            all_results = await agent.executor.wait_all(timeout=5.0)
            bgboom_result = next(
                r for jid, r in all_results.items() if jid.startswith("bgboom_")
            )
            assert "background-kaboom" in (bgboom_result.error or "")

    async def test_history_ops(self, make_creature):
        """One workflow over the three history operations: run a turn,
        ``regenerate_last_response`` opens a new branch, ``edit_and_rerun``
        replaces the user message and re-runs, ``rewind_to`` drops the
        tail. Backed by a real ``SessionStore`` so branch bookkeeping
        (``turn_index`` / ``branch_id``) is exercised.

        Mirrors: the frontend retry / edit / rewind buttons and the CLI
        ``/regen`` command — all three call these same ``Agent`` methods,
        and ``Agent.attach_session_store`` is how the engine wires
        persistence onto a creature.
        """
        from kohakuterrarium.session.store import SessionStore

        creature, recorder = make_creature(
            script=[
                ScriptEntry("original reply", match="first question"),
                ScriptEntry("regenerated reply", match="first question"),
                ScriptEntry("edited reply", match="changed question"),
                # A second turn, then a turn-targeted regenerate of it.
                ScriptEntry("second turn reply", match="second question"),
                ScriptEntry("turn2 regen reply", match="second question"),
            ]
        )
        agent = creature.agent

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            # Attach a real session store — this is the engine's
            # persistence-wiring path.
            store = SessionStore(str(creature.agent.config.agent_path / "s.kohakutr"))
            agent.attach_session_store(store)

            # --- Run the initial turn ----------------------------------
            await _drain_chat(creature, "first question")
            assert "original reply" in _assistant_text(agent)
            assert agent._turn_index == 1
            assert agent._branch_id == 1

            # --- regenerate: same user msg, new branch -----------------
            await agent.regenerate_last_response()
            assert "regenerated reply" in _assistant_text(agent)
            # Still turn 1, but a new branch was opened.
            assert agent._turn_index == 1
            assert agent._branch_id == 2
            # The event log recorded a second branch for turn 1.
            assert agent._max_branch_id_for_turn(1) == 2

            # --- edit_and_rerun: replace the user message, re-run ------
            # System message is index 0, the user message is index 1.
            ok = await agent.edit_and_rerun(
                message_idx=1, new_content="changed question"
            )
            assert ok is True
            assert "edited reply" in _assistant_text(agent)
            # The edited content is the live user message in conversation.
            user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            assert user_msgs[-1].get_text_content() == "changed question"
            # Editing turn 1 opened yet another branch on it.
            assert agent._turn_index == 1
            assert agent._branch_id == 3

            # --- a fresh second turn -----------------------------------
            # A new user input after an edit advances to turn 2; the
            # parent_branch_path now records (turn 1, branch 3) so a
            # later branch switch on turn 1 can hide turn 2's events.
            await _drain_chat(creature, "second question")
            assert "second turn reply" in _assistant_text(agent)
            assert agent._turn_index == 2
            assert agent._branch_id == 1

            # --- regenerate_last_response(turn_index=2): retry an
            #     explicit turn ---------------------------------------
            # Passing ``turn_index`` routes through ``edit_and_rerun``
            # with the turn's recorded user content — the "click retry
            # on a specific assistant message" path. It opens a new
            # branch on turn 2 and exercises the branch-resolution
            # helpers (_user_message_content_for_turn,
            # _user_position_for_turn_index, _live_user_turns).
            await agent.regenerate_last_response(turn_index=2)
            assert "turn2 regen reply" in _assistant_text(agent)
            assert agent._turn_index == 2
            # Turn 2 now has two branches recorded.
            assert agent._max_branch_id_for_turn(2) == 2
            # The live user turns the helper sees are turns 1 and 2.
            assert agent._live_user_turns() == [1, 2]
            # The recorded user_message content for turn 2 is resolvable.
            assert agent._user_message_content_for_turn(2) == "second question"

            # --- rewind_to: drop the tail, no re-run -------------------
            await agent.rewind_to(1)
            roles = [m.role for m in agent.controller.conversation.get_messages()]
            # Only the system prompt survives a rewind to index 1.
            assert roles == ["system"]
            # rewind does not invoke the LLM.
            assert agent.llm.call_count == 5

            # --- workspace: runtime working-directory switch -----------
            # ``agent.workspace`` is wired by ``init_agent_helpers``; it
            # reflects the executor's current working dir.
            agent_path = agent.config.agent_path
            assert agent.workspace.get() == str(
                Path(agent.executor._working_dir).resolve()
            )
            new_dir = agent_path / "subtree"
            new_dir.mkdir()
            resolved = agent.workspace.set(new_dir)
            assert resolved == str(new_dir.resolve())
            # The switch took on the executor — every fresh ToolContext
            # builds from this.
            assert str(agent.executor._working_dir) == str(new_dir.resolve())
            assert agent.workspace.get() == str(new_dir.resolve())
            # The session store's meta picked up the new pwd so resume
            # restores the latest cwd.
            assert store.meta["pwd"] == str(new_dir.resolve())
            # A non-existent path is rejected before any state changes.
            with pytest.raises(ValueError):
                agent.workspace.set(agent_path / "does-not-exist")
            assert str(agent.executor._working_dir) == str(new_dir.resolve())

            # --- plugin_options: per-session plugin override + persist -
            # ``budget`` is a catalog plugin; override its turn_budget and
            # read the merged options back.
            applied = agent.plugin_options.set(
                "budget", {"turn_budget": {"soft": 2, "hard": 5}}
            )
            assert applied["turn_budget"] == {"soft": 2, "hard": 5}
            # The override is tracked on the helper and reflected live on
            # the plugin instance.
            assert agent.plugin_options.get("budget")["turn_budget"] == {
                "soft": 2,
                "hard": 5,
            }
            budget_plugin = agent.plugins.get_plugin("budget")
            assert budget_plugin.options["turn_budget"] == {"soft": 2, "hard": 5}
            # The override was persisted to private session state.
            saved = store.state.get("solo:plugin_options")
            assert saved["budget"]["turn_budget"] == {"soft": 2, "hard": 5}
            # An unknown plugin name is a clean KeyError.
            with pytest.raises(KeyError):
                agent.plugin_options.set("no-such-plugin", {})

            # --- conversation serialization round-trip -----------------
            # The conversation rebuilt from the rewind still has the
            # system prompt; run one more turn so there is real content
            # to round-trip through JSON.
            await _drain_chat(creature, "first question")
            conv = agent.controller.conversation
            chars_before = conv.get_context_length()
            assert chars_before > 0
            assert len(conv) == len(conv.get_messages())
            assert bool(conv) is True
            json_blob = conv.to_json()
            restored = Conversation.from_json(json_blob)
            # The restored conversation carries the exact same messages
            # in the same order with the same text payloads.
            assert [m.role for m in restored.get_messages()] == [
                m.role for m in conv.get_messages()
            ]
            assert restored.get_context_length() == chars_before
            assert (
                restored.get_last_assistant_message().get_text_content()
                == conv.get_last_assistant_message().get_text_content()
            )
            # ``find_last_user_index`` points at the live user message.
            last_user_idx = conv.find_last_user_index()
            assert conv.get_messages()[last_user_idx].role == "user"

            # --- edit_and_rerun rejects an out-of-range index ----------
            # A message_idx past the end resolves to None → returns False
            # and runs no LLM call.
            calls_pre = agent.llm.call_count
            bad = await agent.edit_and_rerun(message_idx=999, new_content="nope")
            assert bad is False
            assert agent.llm.call_count == calls_pre

            # --- native_tool_options on a non-native tool is rejected --
            # ``echo``-style tools are NOT provider-native, so setting
            # native options for any unknown native tool raises.
            with pytest.raises(ValueError):
                agent.native_tool_options.set("definitely-not-native", {"k": "v"})
            # The override map stays empty after the rejected set.
            assert agent.native_tool_options.list() == {}

            # --- branch-view replay: reseat the conversation -----------
            # ``_reload_conversation_under_branch_view`` is the frontend
            # "select an older branch" path: it replays the event log
            # under a chosen ``branch_view`` and reseats the in-memory
            # conversation + agent turn/branch state to that subtree's
            # leaf. Selecting turn 1's branch 1 (the original) reseats
            # the agent onto turn 1 / branch 1.
            agent._reload_conversation_under_branch_view({1: 1})
            assert agent._turn_index == 1
            assert agent._branch_id == 1
            # The replayed conversation carries turn 1's branch-1 user
            # message ("first question") and original assistant reply.
            replayed = " ".join(
                m.get_text_content()
                for m in agent.controller.conversation.get_messages()
            )
            assert "first question" in replayed
            assert "original reply" in replayed

    async def test_inject_event_and_unified_trigger_model(self, make_creature):
        """The unified ``TriggerEvent`` model: a non-user-input event
        (a synthetic ``tool_complete``) injected straight into the
        agent drives a full controller turn, exactly like a real
        background-tool completion would.

        Mirrors: ``Agent._on_bg_complete`` / channel-trigger fan-out —
        both reach the controller loop through ``_process_event`` with a
        non-``user_input`` ``TriggerEvent``, not through ``inject_input``.
        """
        creature, recorder = make_creature(
            script=[
                ScriptEntry("handled the completion", match="tool-output-xyz"),
                ScriptEntry("acknowledged", match="now talk"),
                # The hot-plugged trigger fires this prompt as its event
                # content; the controller answers it as a fresh turn.
                ScriptEntry("trigger handled", match="wake up please"),
            ]
        )
        agent = creature.agent

        async with Terrarium() as engine:
            await engine.add_creature(creature)

            # A tool_complete event — the same shape the executor's
            # completion callback builds for a finished background job.
            evt = create_tool_complete_event(
                job_id="bash_42", content="tool-output-xyz", exit_code=0
            )
            assert evt.type == "tool_complete"
            await agent.inject_event(evt)

            # The controller ran one turn off the injected event.
            assert agent.llm.call_count == 1
            assert "handled the completion" in _assistant_text(agent)
            # tool_complete is NOT user input: turn_index stays at 0
            # (only ``user_input`` events bump it in ``_process_event``).
            assert agent._turn_index == 0

            # And a plain user_input event through the same _process_event
            # path DOES advance the turn — proving the branch logic keys
            # on event.type, not on the entry point.
            await agent.inject_event(create_user_input_event("now talk"))
            assert agent._turn_index == 1
            assert agent.llm.call_count == 2

            # --- Hot-plug a real trigger on the running creature --------
            # ``Agent.add_trigger`` goes through the real TriggerManager:
            # it starts the trigger and spawns its ``_run_loop`` task.
            # When the trigger emits, the run loop drives ``_process_event``
            # exactly like a TimerTrigger would — the unified event model.
            trig = _OneShotTrigger("wake up please")
            trigger_id = await agent.add_trigger(trig)
            assert trigger_id.startswith("trigger_")
            # The manager reports it as a live trigger.
            info = agent.trigger_manager.get(trigger_id)
            assert info is not None
            assert info.running is True
            assert any(t.trigger_id == trigger_id for t in agent.trigger_manager.list())

            # Wait for the trigger to fire and its event to drive a turn.
            await asyncio.wait_for(trig.fired.wait(), timeout=5.0)
            for _ in range(100):
                if "trigger handled" in _assistant_text(agent):
                    break
                await asyncio.sleep(0.02)
            assert "trigger handled" in _assistant_text(agent)
            assert agent.llm.call_count == 3
            # The trigger-fired turn is a non-user-input event: turn_index
            # is still 1 (only ``user_input`` bumps it).
            assert agent._turn_index == 1

            # --- Remove the trigger on the running creature -------------
            removed = await agent.remove_trigger(trigger_id)
            assert removed is True
            assert agent.trigger_manager.get(trigger_id) is None
            # Removing a non-existent trigger is a clean False.
            assert await agent.remove_trigger("trigger_nonexistent") is False

            # --- post_llm_call plugin rewrites the assistant text ------
            # Register a plugin whose ``post_llm_call`` appends a suffix.
            # The controller's post-LLM chain mutates the stored
            # assistant message in place and emits an
            # ``assistant_message_edited`` activity.
            agent.plugins.register(_RewriteResponsePlugin())
            post_llm = ScriptedLLM(
                [ScriptEntry("base assistant answer", match="rewrite this")]
            )
            agent.llm = post_llm
            agent.controller.llm = post_llm
            await agent.inject_input("rewrite this please", source="chat")
            # The stored assistant message carries the plugin's suffix —
            # the rewrite landed in the conversation, not just the stream.
            assert _assistant_text(agent) == "base assistant answer [reviewed]"
            # The router surfaced the edit-marker activity.
            assert len(recorder.activities_of_type("assistant_message_edited")) >= 1

            # --- batched stackable events through one controller turn --
            # The controller batches simultaneously-queued stackable
            # events into ONE LLM round (the "multiple triggers fired at
            # once" path). Bind a fresh scripted LLM, push two
            # tool_complete events straight onto the controller queue,
            # then run a single turn: both event bodies must appear in
            # the user message the LLM saw.
            batch_llm = ScriptedLLM([ScriptEntry("batched both", match="alpha-evt")])
            agent.llm = batch_llm
            agent.controller.llm = batch_llm
            agent.controller.push_event_sync(
                create_tool_complete_event(
                    job_id="bash_a", content="alpha-evt", exit_code=0
                )
            )
            agent.controller.push_event_sync(
                create_tool_complete_event(
                    job_id="bash_b", content="beta-evt", exit_code=0
                )
            )
            async for _ in agent.controller.run_once():
                pass
            # ONE LLM round consumed BOTH queued events.
            assert batch_llm.call_count == 1
            batch_user_msgs = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user"
            ]
            last_batch_user = batch_user_msgs[-1].get_text_content()
            # Both event bodies were folded into the single user message.
            assert "alpha-evt" in last_batch_user
            assert "beta-evt" in last_batch_user

            # --- multimodal user input through inject_input ------------
            # ``inject_input`` accepts a list of ContentParts; the
            # controller's ``_format_events_for_context`` keeps the text
            # part and threads image parts through as multimodal content.
            mm_llm = ScriptedLLM(
                [ScriptEntry("saw the picture", match="describe-this")]
            )
            agent.llm = mm_llm
            agent.controller.llm = mm_llm
            await agent.inject_input(
                [
                    TextPart(text="describe-this image please"),
                    ImagePart(url="data:image/png;base64,iVBORw0KGgo=", detail="low"),
                ],
                source="chat",
            )
            assert mm_llm.call_count == 1
            assert "saw the picture" in _assistant_text(agent)
            # The conversation recorded a multimodal user message — the
            # image part survived into the stored message content.
            mm_user = [
                m
                for m in agent.controller.conversation.get_messages()
                if m.role == "user" and isinstance(m.content, list)
            ]
            assert mm_user, "no multimodal user message recorded"
            assert any(isinstance(p, ImagePart) for p in mm_user[-1].content)
            # ``get_image_count`` sees exactly the one image we injected.
            assert agent.controller.conversation.get_image_count() == 1

            # --- inline-file user input through inject_input -----------
            # A ``FilePart`` carrying literal ``content`` (no path, not
            # flagged ``is_inline``) is resolved by the controller's
            # ``_resolve_message_files`` straight into a plain text part
            # — the ``File: <name>\n<content>`` form the provider sees.
            # The scripted reply is keyed on a substring of that content.
            file_llm = ScriptedLLM(
                [ScriptEntry("read the attached file", match="inline-file-marker")]
            )
            agent.llm = file_llm
            agent.controller.llm = file_llm
            await agent.inject_input(
                [
                    TextPart(text="please read this"),
                    FilePart(
                        name="notes.txt",
                        content="inline-file-marker: hello from the file",
                    ),
                ],
                source="chat",
            )
            assert file_llm.call_count == 1
            assert "read the attached file" in _assistant_text(agent)
            # The inline file content was resolved into the message the
            # provider actually saw — the ScriptedLLM matched on it.
            file_call_msgs = file_llm.call_log[-1]
            joined_file_call = " ".join(
                str(m.get("content", "")) for m in file_call_msgs
            )
            assert "inline-file-marker" in joined_file_call
