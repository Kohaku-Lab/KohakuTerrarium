"""Unit tests for :mod:`kohakuterrarium.modules.subagent.base`.

Behavior-first: SubAgent runs a real conversation loop against a
ScriptedLLM, executes parsed tool calls through a real Registry,
respects max_turns / timeout / cancellation, charges the iteration
budget, and surfaces failures as a failed SubAgentResult rather than
raising.
"""

from kohakuterrarium.core.budget import IterationBudget
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM


class _EchoTool(BaseTool):
    """Records every call; returns the text it was given."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo the text arg"

    async def _execute(self, args, **kwargs):
        self.calls.append(args)
        return ToolResult(output=f"echoed: {args.get('text', '')}")


class _FailTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "failtool"

    @property
    def description(self) -> str:
        return "always fails"

    async def _execute(self, args, **kwargs):
        return ToolResult(error="tool blew up")


def _registry(*tools):
    reg = Registry()
    for tool in tools:
        reg.register_tool(tool)
    return reg


def _bracket_call(name, **args):
    arg_lines = "".join(f"@@{k}={v}\n" for k, v in args.items())
    return f"[/{name}]\n{arg_lines}[{name}/]"


class TestBasicRun:
    async def test_no_tool_call_finishes_in_one_turn(self):
        llm = ScriptedLLM(["All done — no tools needed."])
        sa = SubAgent(SubAgentConfig(name="x", max_turns=3), _registry(), llm)
        result = await sa.run("do the thing")
        assert result.success is True
        assert result.turns == 1
        assert result.output == "All done — no tools needed."

    async def test_tool_call_executed_then_loop_continues(self):
        echo = _EchoTool()
        # Turn 1 emits a tool call; turn 2 finishes plain.
        llm = ScriptedLLM(
            [
                _bracket_call("echo", text="hello"),
                "Tool ran, finishing up.",
            ]
        )
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=5),
            _registry(echo),
            llm,
            tool_format="bracket",
        )
        result = await sa.run("use the echo tool")
        assert result.success is True
        # The tool was actually executed with the parsed args.
        assert echo.calls == [{"text": "hello"}]
        assert result.metadata["tools_used"] == ["echo"]
        assert result.turns == 2

    async def test_failing_tool_still_completes_run(self):
        # A tool returning an error result does NOT crash the run — the
        # error is fed back to the model as a tool result.
        llm = ScriptedLLM(
            [
                _bracket_call("failtool"),
                "Saw the failure, stopping.",
            ]
        )
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["failtool"], max_turns=5),
            _registry(_FailTool()),
            llm,
            tool_format="bracket",
        )
        result = await sa.run("call the failing tool")
        # The run itself succeeds even though the tool failed.
        assert result.success is True
        assert "failtool" in result.metadata["tools_used"]


class TestLimits:
    async def test_max_turns_caps_the_loop(self):
        # The LLM keeps emitting tool calls; max_turns=2 caps it.
        echo = _EchoTool()
        llm = ScriptedLLM([_bracket_call("echo", text="loop") for _ in range(10)])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=2),
            _registry(echo),
            llm,
            tool_format="bracket",
        )
        result = await sa.run("loop forever")
        assert result.turns == 2

    async def test_missing_tool_recorded_and_noted_in_prompt(self):
        # A tool requested in config but absent from the parent registry
        # is tracked and surfaced; the sub-agent still runs.
        llm = ScriptedLLM(["finished"])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["ghost_tool"]),
            _registry(),
            llm,
        )
        assert sa._missing_tools == ["ghost_tool"]
        prompt = sa._build_system_prompt()
        assert "Unavailable Tools" in prompt
        assert "ghost_tool" in prompt


class TestCancellation:
    async def test_cancel_during_stream_returns_interrupted_result(self):
        # The sub-agent cancels itself mid-stream; the run loop must exit
        # at the post-turn checkpoint with an interrupted result.
        class _SelfCancellingLLM:
            model = "x"

            def __init__(self, subagent_ref):
                self._sa = subagent_ref

            async def chat(self, messages, *, stream=True, **kwargs):
                yield "partial output "
                # Cancel happens between chunks — the loop checkpoint sees it.
                self._sa[0].cancel()
                yield "more"

        holder: list = []
        sa = SubAgent(
            SubAgentConfig(name="x", max_turns=3),
            _registry(),
            _SelfCancellingLLM(holder),
        )
        holder.append(sa)
        result = await sa.run("task")
        assert result.success is False
        assert result.interrupted is True
        assert result.error == "User manually interrupted this job."

    def test_cancel_sets_flags(self):
        llm = ScriptedLLM(["x"])
        sa = SubAgent(SubAgentConfig(name="x"), _registry(), llm)
        sa.cancel()
        assert sa._cancelled is True
        assert sa.is_running is False


class TestBudget:
    async def test_exhausted_budget_returns_failed_result(self):
        # A zero-remaining budget fails the run before the first LLM call.
        llm = ScriptedLLM(["never reached"])
        sa = SubAgent(SubAgentConfig(name="x", max_turns=5), _registry(), llm)
        sa.iteration_budget = IterationBudget(remaining=0, total=3)
        result = await sa.run("task")
        assert result.success is False
        assert "BudgetExhausted" in result.error
        assert result.metadata["budget_exhausted"] is True

    async def test_budget_consumed_per_turn(self):
        # Each turn charges one unit; a budget of 1 allows exactly one turn.
        echo = _EchoTool()
        llm = ScriptedLLM([_bracket_call("echo", text="x") for _ in range(5)])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=10),
            _registry(echo),
            llm,
            tool_format="bracket",
        )
        sa.iteration_budget = IterationBudget(remaining=1, total=1)
        result = await sa.run("loop")
        # One turn ran, then the budget exhausted on the second.
        assert result.success is False
        assert "BudgetExhausted" in result.error
        assert sa._turns == 1


class TestErrorHandling:
    async def test_llm_exception_becomes_failed_result(self):
        # An LLM that raises mid-stream must not propagate — run() catches
        # it and returns a failed SubAgentResult.
        class _BoomLLM:
            model = "boom"

            async def chat(self, messages, *, stream=True, **kwargs):
                raise RuntimeError("provider exploded")
                yield  # pragma: no cover

        sa = SubAgent(SubAgentConfig(name="x", max_turns=2), _registry(), _BoomLLM())
        result = await sa.run("task")
        assert result.success is False
        assert "provider exploded" in result.error

    async def test_timeout_returns_failed_result(self):
        # A sub-agent with a tiny timeout against a slow LLM times out
        # cleanly instead of hanging or raising.
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                await asyncio.sleep(5)
                yield "too late"

        sa = SubAgent(
            SubAgentConfig(name="x", max_turns=2, timeout=0.1),
            _registry(),
            _SlowLLM(),
        )
        result = await sa.run("task")
        assert result.success is False
        assert "Timed out" in result.error


class _NativeToolCall:
    """Stand-in for a provider native tool call object."""

    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.name = name
        self.arguments = arguments

    def parsed_arguments(self):
        import json

        return json.loads(self.arguments)


class _NativeLLM:
    """Native-mode LLM: first turn emits a tool call, second finishes."""

    model = "native-model"

    def __init__(self):
        self._turn = 0
        self.last_tool_calls: list = []
        self.last_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cached_tokens": 3,
        }

    async def chat(self, messages, *, stream=True, tools=None, **kwargs):
        self._turn += 1
        if self._turn == 1:
            self.last_tool_calls = [
                _NativeToolCall("call-1", "echo", '{"text": "native-hi"}')
            ]
            yield ""
        else:
            self.last_tool_calls = []
            yield "native run complete"


class TestNativeMode:
    async def test_native_turn_executes_tool_and_accumulates_tokens(self):
        echo = _EchoTool()
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=5),
            _registry(echo),
            _NativeLLM(),
            tool_format="native",
        )
        result = await sa.run("native task")
        assert result.success is True
        # The native tool call was parsed and executed.
        assert echo.calls == [{"text": "native-hi", "_tool_call_id": "call-1"}]
        # Token usage from last_usage is accumulated across turns.
        assert result.total_tokens == 30  # 15 per turn × 2 turns
        assert result.cached_tokens == 6


class _UsageLLM:
    """Text-mode LLM that reports token usage."""

    model = "usage-model"

    def __init__(self, response="done"):
        self._response = response
        self.last_usage = {
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
            "cached_tokens": 4,
        }

    async def chat(self, messages, *, stream=True, **kwargs):
        yield self._response


class TestTokenAccounting:
    async def test_text_turn_accumulates_usage_into_result(self):
        sa = SubAgent(
            SubAgentConfig(name="x", max_turns=1),
            _registry(),
            _UsageLLM(),
        )
        result = await sa.run("task")
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 8
        assert result.total_tokens == 28
        assert result.cached_tokens == 4

    async def test_token_update_activity_emitted_to_parent(self):
        activities: list[tuple] = []
        sa = SubAgent(
            SubAgentConfig(name="x", max_turns=1),
            _registry(),
            _UsageLLM(),
        )
        sa.on_tool_activity = lambda *args: activities.append(args)
        await sa.run("task")
        # A token_update activity is forwarded to the parent callback.
        assert any(a[0] == "token_update" for a in activities)


class TestSessionStorePersistence:
    async def test_conversation_saved_to_session_store_on_success(self):
        saved: list[dict] = []

        class _FakeStore:
            def save_subagent(self, **kwargs):
                saved.append(kwargs)

        sa = SubAgent(
            SubAgentConfig(name="explore", max_turns=1),
            _registry(),
            ScriptedLLM(["found it"]),
        )
        sa._session_store = _FakeStore()
        sa._parent_name = "controller"
        sa._run_index = 2
        result = await sa.run("find the bug")
        assert result.success is True
        # The sub-agent conversation was persisted with the right lineage.
        assert len(saved) == 1
        assert saved[0]["parent"] == "controller"
        assert saved[0]["name"] == "explore"
        assert saved[0]["run"] == 2

    async def test_session_store_save_failure_does_not_fail_run(self):
        class _BadStore:
            def save_subagent(self, **kwargs):
                raise RuntimeError("disk full")

        sa = SubAgent(
            SubAgentConfig(name="explore", max_turns=1),
            _registry(),
            ScriptedLLM(["done anyway"]),
        )
        sa._session_store = _BadStore()
        result = await sa.run("task")
        # A persistence failure is logged but the run still succeeds.
        assert result.success is True
        assert result.output == "done anyway"


class TestPluginIntegration:
    async def test_pre_tool_execute_plugin_can_block_a_tool(self):
        # A PluginBlockError from pre_tool_execute becomes the tool result
        # text — the run continues, the tool never actually executes.
        from kohakuterrarium.modules.plugin.base import (
            BasePlugin,
            PluginBlockError,
        )
        from kohakuterrarium.modules.plugin.manager import PluginManager

        class _BlockPlugin(BasePlugin):
            name = "blocker"

            async def pre_tool_execute(self, args, **kwargs):
                raise PluginBlockError("tool denied by policy")

        echo = _EchoTool()
        pm = PluginManager()
        pm.register(_BlockPlugin())
        llm = ScriptedLLM([_bracket_call("echo", text="hi"), "saw the block, done"])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=5),
            _registry(echo),
            llm,
            tool_format="bracket",
            plugin_manager=pm,
        )
        result = await sa.run("call echo")
        assert result.success is True
        # The tool was blocked — it never recorded a call.
        assert echo.calls == []

    async def test_post_llm_plugin_rewrites_output(self):
        # A post_llm_call plugin can rewrite the assistant output; the
        # rewritten text is what lands in the result.
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager

        class _RewritePlugin(BasePlugin):
            name = "rewriter"

            async def post_llm_call(self, messages, response, usage, **kwargs):
                return response.upper()

        pm = PluginManager()
        pm.register(_RewritePlugin())
        sa = SubAgent(
            SubAgentConfig(name="x", max_turns=1),
            _registry(),
            ScriptedLLM(["quiet response"]),
            plugin_manager=pm,
        )
        result = await sa.run("task")
        assert result.output == "QUIET RESPONSE"


class TestToolActivityCallbacks:
    async def test_on_tool_activity_reports_start_and_done(self):
        echo = _EchoTool()
        activities: list[tuple] = []
        llm = ScriptedLLM([_bracket_call("echo", text="hi"), "finished"])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=5),
            _registry(echo),
            llm,
            tool_format="bracket",
        )
        sa.on_tool_activity = lambda *args: activities.append(args)
        await sa.run("call echo")
        kinds = {a[0] for a in activities}
        # Both the start and the done activity were reported to the parent.
        assert "tool_start" in kinds
        assert "tool_done" in kinds

    async def test_on_tool_activity_reports_tool_error(self):
        activities: list[tuple] = []
        llm = ScriptedLLM([_bracket_call("failtool"), "done"])
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["failtool"], max_turns=5),
            _registry(_FailTool()),
            llm,
            tool_format="bracket",
        )
        sa.on_tool_activity = lambda *args: activities.append(args)
        await sa.run("call failtool")
        assert any(a[0] == "tool_error" for a in activities)


class TestToolExecutionEdgeCases:
    async def test_unavailable_tool_reports_error_in_results(self):
        # The parser knows a tool name but the registry doesn't have it.
        echo = _EchoTool()
        sa = SubAgent(
            SubAgentConfig(name="x", tools=["echo"], max_turns=2),
            _registry(echo),
            ScriptedLLM(["done"]),
            tool_format="bracket",
        )
        from kohakuterrarium.parsing import ToolCallEvent

        results = await sa._execute_tools(
            [ToolCallEvent(name="not_registered", args={})]
        )
        assert "Error: Tool not available" in results

    async def test_str_returning_tool_is_wrapped(self):
        # A tool whose execute() yields a bare str (not ToolResult) is
        # salvaged into a result block, not crashed.
        class _StrTool(BaseTool):
            @property
            def tool_name(self):
                return "stringy"

            @property
            def description(self):
                return "returns str"

            async def execute(self, args, context=None):
                return "raw string"

            async def _execute(self, args, **kwargs):  # pragma: no cover
                return "raw string"

        sa = SubAgent(
            SubAgentConfig(name="x", tools=["stringy"], max_turns=2),
            _registry(_StrTool()),
            ScriptedLLM(["done"]),
            tool_format="bracket",
        )
        from kohakuterrarium.parsing import ToolCallEvent

        results = await sa._execute_tools([ToolCallEvent(name="stringy", args={})])
        assert "raw string" in results

    async def test_tool_raising_exception_is_caught(self):
        class _RaiseTool(BaseTool):
            @property
            def tool_name(self):
                return "raiser"

            @property
            def description(self):
                return "raises"

            async def execute(self, args, context=None):
                raise RuntimeError("tool internal error")

            async def _execute(self, args, **kwargs):  # pragma: no cover
                raise RuntimeError("tool internal error")

        sa = SubAgent(
            SubAgentConfig(name="x", tools=["raiser"], max_turns=2),
            _registry(_RaiseTool()),
            ScriptedLLM(["done"]),
            tool_format="bracket",
        )
        from kohakuterrarium.parsing import ToolCallEvent

        results = await sa._execute_tools([ToolCallEvent(name="raiser", args={})])
        # The exception is caught and surfaced as an error block.
        assert "Error: tool internal error" in results


class TestParserFormatResolution:
    def test_xml_format_resolves(self):
        from kohakuterrarium.parsing.format import XML_FORMAT

        assert SubAgent._resolve_parser_format("xml") is XML_FORMAT

    def test_native_and_none_resolve_to_bracket(self):
        from kohakuterrarium.parsing.format import BRACKET_FORMAT

        assert SubAgent._resolve_parser_format("native") is BRACKET_FORMAT
        assert SubAgent._resolve_parser_format(None) is BRACKET_FORMAT
        assert SubAgent._resolve_parser_format("unknown") is BRACKET_FORMAT
