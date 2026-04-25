"""
Sub-agent base class.

A sub-agent is a nested agent with its own controller, limited tool access,
and configurable output routing.
"""

import asyncio
from datetime import datetime
from typing import Any

from kohakuterrarium.core.budget import BudgetExhausted, IterationBudget
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.result import (  # noqa: F401 – re-exported for backward compat
    SubAgentJob,
    SubAgentResult,
    build_subagent_framework_hints,
)
from kohakuterrarium.parsing import ParserConfig, StreamParser, TextEvent, ToolCallEvent
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SubAgent:
    """Nested agent with limited capabilities.

    A sub-agent runs with its own controller and tool access, but returns
    results to the parent controller (unless output_to=external).
    """

    def __init__(
        self,
        config: SubAgentConfig,
        parent_registry: Registry,
        llm: LLMProvider,
        agent_path: Any = None,
        tool_format: str | None = None,
    ):
        self.config = config
        self.parent_registry = parent_registry
        self.llm = llm
        self.agent_path = agent_path
        self.tool_format = tool_format

        # Optional callback for reporting tool activity to parent
        self.on_tool_activity: Any = None

        # Parent's tool context builder (inherited environment, working_dir, etc.)
        self._build_tool_context: Any = None

        # Optional session store for persisting sub-agent conversations
        self._session_store: Any = None
        self._parent_name: str = ""
        self._run_index: int = 0

        # Shared iteration budget — set by SubAgentManager.spawn() based on
        # parent's budget and this config's budget_inherit / budget_allocation
        # fields. ``None`` means "no budget enforcement" (today's behavior).
        self.iteration_budget: IterationBudget | None = None

        # Create limited registry with only allowed tools
        self.registry = self._create_limited_registry()

        # Create executor for this sub-agent
        self.executor = Executor()
        for tool_name in self.registry.list_tools():
            tool = self.registry.get_tool(tool_name)
            if tool:
                self.executor.register_tool(tool)

        # Conversation for this sub-agent
        self.conversation = Conversation()

        # Token usage tracking
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        # Wave B: track prompt-cache hits so the parent's
        # ``subagent_done`` event can record them (audit finding A).
        self._cached_tokens = 0

        # Resolve tool call format for the parser
        self._is_native = tool_format == "native"
        parser_tool_format = self._resolve_parser_format(tool_format)

        # Stream parser with known tools from registry
        self._parser_config = ParserConfig(
            known_tools=set(self.registry.list_tools()),
            tool_format=parser_tool_format,
        )
        self._parser = StreamParser(self._parser_config)

        # State
        self._running = False
        self._cancelled = False
        self._start_time: datetime | None = None
        self._turns = 0

        logger.debug(
            "SubAgent created",
            subagent_name=config.name,
            tools=config.tools,
            tool_format=tool_format or "bracket",
        )

    @staticmethod
    def _resolve_parser_format(tool_format: str | None) -> ToolCallFormat:
        """Resolve a tool_format string to a ToolCallFormat instance."""
        match tool_format:
            case "xml":
                return XML_FORMAT
            case "native" | None | "bracket":
                return BRACKET_FORMAT
            case _:
                return BRACKET_FORMAT

    def _create_limited_registry(self) -> Registry:
        """Create registry with only allowed tools."""
        limited = Registry()
        self._missing_tools: list[str] = []

        for tool_name in self.config.tools:
            tool = self.parent_registry.get_tool(tool_name)
            if tool:
                limited.register_tool(tool)
            else:
                self._missing_tools.append(tool_name)
                logger.warning(
                    "Tool not found in parent registry",
                    tool_name=tool_name,
                    subagent=self.config.name,
                )

        return limited

    def _build_system_prompt(self) -> str:
        """Build complete system prompt with framework hints and tool list."""
        parts = []

        base_prompt = self.config.load_prompt(self.agent_path)
        parts.append(base_prompt)

        tool_names = self.registry.list_tools()
        if tool_names:
            tool_lines = ["## Available Tools", ""]
            for name in tool_names:
                info = self.registry.get_tool_info(name)
                desc = info.description if info else "Tool"
                tool_lines.append(f"- `{name}`: {desc}")
            parts.append("\n".join(tool_lines))

        if self._missing_tools:
            missing_note = (
                "## Unavailable Tools\n\n"
                "The following tools were requested but are not available: "
                + ", ".join(f"`{t}`" for t in self._missing_tools)
                + "\nDo NOT attempt to call these tools. Work with what is available."
            )
            parts.append(missing_note)

        parser_fmt = self._resolve_parser_format(self.tool_format)
        parts.append(build_subagent_framework_hints(self.tool_format, parser_fmt))

        result = "\n\n".join(parts)
        logger.info(
            "Sub-agent system prompt built",
            subagent_name=self.config.name,
            tool_count=len(tool_names),
            prompt_length=len(result),
        )
        return result

    async def run(self, task: str) -> SubAgentResult:
        """Execute the sub-agent with a task."""
        self._running = True
        self._start_time = datetime.now()
        self._turns = 0

        try:
            if self.config.timeout > 0:
                return await asyncio.wait_for(
                    self._run_internal(task),
                    timeout=self.config.timeout,
                )
            else:
                return await self._run_internal(task)
        except asyncio.TimeoutError:
            logger.warning(
                "Sub-agent timed out",
                subagent_name=self.config.name,
                timeout=self.config.timeout,
            )
            return SubAgentResult(
                success=False,
                error=f"Timed out after {self.config.timeout}s",
                turns=self._turns,
                duration=self._calculate_duration(),
            )
        except Exception as e:
            logger.error(
                "Sub-agent error",
                subagent_name=self.config.name,
                error=str(e),
            )
            return SubAgentResult(
                success=False,
                error=str(e),
                turns=self._turns,
                duration=self._calculate_duration(),
            )
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # Internal run logic (split into focused helpers)
    # ------------------------------------------------------------------

    async def _run_internal(self, task: str) -> SubAgentResult:
        """Internal run logic. Runs conversation loop with tool execution."""
        self._setup_conversation(task)

        native_tool_schemas = None
        if self._is_native:
            native_tool_schemas = build_tool_schemas(self.registry)

        output_parts: list[str] = []
        tools_used: list[str] = []

        while self.config.max_turns == 0 or self._turns < self.config.max_turns:
            if self._cancelled:
                return SubAgentResult(
                    success=False,
                    error="User manually interrupted this job.",
                    interrupted=True,
                    turns=self._turns,
                    duration=self._calculate_duration(),
                    total_tokens=self._total_tokens,
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cached_tokens=self._cached_tokens,
                    metadata={"tools_used": tools_used},
                )
            # Charge one unit against the shared iteration budget before
            # spending an LLM call. On exhaustion we return a failed
            # SubAgentResult so the parent controller sees a tool-result
            # error and can decide how to proceed.
            if self.iteration_budget is not None:
                exhausted = self._charge_budget_or_fail(tools_used)
                if exhausted is not None:
                    return exhausted

            self._turns += 1
            logger.debug(
                "Sub-agent turn started",
                subagent_name=self.config.name,
                turn=self._turns,
            )

            tool_calls, turn_output = await self._run_single_turn(native_tool_schemas)
            output_parts.extend(turn_output)

            if self._cancelled:
                return SubAgentResult(
                    success=False,
                    error="User manually interrupted this job.",
                    interrupted=True,
                    turns=self._turns,
                    duration=self._calculate_duration(),
                    total_tokens=self._total_tokens,
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cached_tokens=self._cached_tokens,
                    metadata={"tools_used": tools_used},
                )

            if not tool_calls:
                logger.info(
                    "Sub-agent no tools called, finishing",
                    subagent_name=self.config.name,
                )
                break

            tools_used.extend(tc.name for tc in tool_calls)
            tool_results = await self._execute_and_report_tools(tool_calls)
            self._append_tool_results(tool_calls, tool_results)

        return self._build_result(output_parts, tools_used)

    def _setup_conversation(self, task: str) -> None:
        """Initialize conversation with system prompt and task."""
        self.conversation = Conversation()
        system_prompt = self._build_system_prompt()
        self.conversation.append("system", system_prompt)
        self.conversation.append("user", task)

    async def _run_single_turn(
        self, native_tool_schemas: Any
    ) -> tuple[list[ToolCallEvent], list[str]]:
        """Run one LLM turn. Returns (tool_calls, output_text_parts)."""
        messages = self.conversation.to_messages()
        if self._is_native and native_tool_schemas:
            return await self._run_native_turn(messages, native_tool_schemas)
        return await self._run_text_turn(messages)

    async def _run_native_turn(
        self, messages: list[dict], tool_schemas: Any
    ) -> tuple[list[ToolCallEvent], list[str]]:
        """Run one native-mode LLM turn."""
        assistant_content = ""
        output_parts: list[str] = []
        tool_calls: list[ToolCallEvent] = []

        async for chunk in self.llm.chat(
            messages, stream=True, tools=tool_schemas or None
        ):
            if self._cancelled:
                break
            assistant_content += chunk
            if chunk:
                output_parts.append(chunk)

        native_calls = (
            self.llm.last_tool_calls if hasattr(self.llm, "last_tool_calls") else []
        )

        if native_calls:
            tool_calls_data = []
            for tc in native_calls:
                tool_calls_data.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                )
                tool_calls.append(
                    ToolCallEvent(
                        name=tc.name,
                        args={**tc.parsed_arguments(), "_tool_call_id": tc.id},
                        raw=tc.arguments,
                    )
                )
                logger.info(
                    "Sub-agent native tool call",
                    subagent_name=self.config.name,
                    tool_name=tc.name,
                )
            self.conversation.append(
                "assistant",
                assistant_content or "",
                tool_calls=tool_calls_data,
            )
        else:
            self.conversation.append("assistant", assistant_content)

        self._log_turn_preview(assistant_content)
        self._accumulate_tokens()
        return tool_calls, output_parts

    async def _run_text_turn(
        self, messages: list[dict]
    ) -> tuple[list[ToolCallEvent], list[str]]:
        """Run one custom-format LLM turn with stream parsing."""
        self._parser = StreamParser(self._parser_config)
        assistant_content = ""
        output_parts: list[str] = []
        tool_calls: list[ToolCallEvent] = []

        async for chunk in self.llm.chat(messages, stream=True):
            if self._cancelled:
                break
            assistant_content += chunk
            for event in self._parser.feed(chunk):
                if isinstance(event, ToolCallEvent):
                    tool_calls.append(event)
                elif isinstance(event, TextEvent):
                    output_parts.append(event.text)

        for event in self._parser.flush():
            if isinstance(event, ToolCallEvent):
                tool_calls.append(event)
            elif isinstance(event, TextEvent):
                output_parts.append(event.text)

        self.conversation.append("assistant", assistant_content)
        self._log_turn_preview(assistant_content)
        self._accumulate_tokens()
        return tool_calls, output_parts

    def _accumulate_tokens(self) -> None:
        """Accumulate token usage from the last LLM call."""
        usage = getattr(self.llm, "last_usage", None)
        if usage and isinstance(usage, dict):
            self._prompt_tokens += usage.get("prompt_tokens", 0)
            self._completion_tokens += usage.get("completion_tokens", 0)
            self._total_tokens += usage.get("total_tokens", 0)
            # Wave B audit finding A: provider fills
            # ``last_usage["cached_tokens"]`` (see llm/openai.py:242
            # and codex_provider.py:555), but the sub-agent used to
            # drop it. Track it so the parent's ``subagent_done``
            # event carries the real cached-tokens count.
            self._cached_tokens += usage.get("cached_tokens", 0)
        # Emit running token totals to parent
        if self.on_tool_activity and self._total_tokens > 0:
            self.on_tool_activity(
                "token_update",
                "",
                f"tokens: {self._prompt_tokens} in, {self._completion_tokens} out",
                {
                    "prompt_tokens": self._prompt_tokens,
                    "completion_tokens": self._completion_tokens,
                    "total_tokens": self._total_tokens,
                    "cached_tokens": self._cached_tokens,
                },
            )

    def _log_turn_preview(self, assistant_content: str) -> None:
        """Log a preview of the LLM response for debugging."""
        preview = assistant_content[:200].replace("\n", " ")
        logger.debug(
            "Sub-agent LLM response",
            subagent_name=self.config.name,
            turn=self._turns,
            preview=preview + ("..." if len(assistant_content) > 200 else ""),
        )

    async def _execute_and_report_tools(self, tool_calls: list[ToolCallEvent]) -> str:
        """Execute tools, notifying parent of start/done via callback."""
        logger.info(
            "Sub-agent executing tools",
            subagent_name=self.config.name,
            tool_count=len(tool_calls),
            tools=[tc.name for tc in tool_calls],
        )

        if self.on_tool_activity:
            for tc in tool_calls:
                tc_args = {k: v for k, v in tc.args.items() if not k.startswith("_")}
                args_preview = ""
                if tc_args:
                    parts = [f"{k}={str(v)[:80]}" for k, v in tc_args.items()]
                    args_preview = " ".join(parts)[:120]
                self.on_tool_activity("tool_start", tc.name, args_preview)

        tool_results = await self._execute_tools(tool_calls)

        # Emit per-tool done/error events with result previews
        if self.on_tool_activity:
            for tc in tool_calls:
                # Find the matching result block in tool_results
                prefix = f"[{tc.name}]"
                for block in tool_results.split("\n\n"):
                    if block.startswith(prefix):
                        if "Error:" in block:
                            error_msg = block.split("Error:", 1)[-1].strip()[:100]
                            self.on_tool_activity("tool_error", tc.name, error_msg)
                        else:
                            preview = block[len(prefix) :].strip()[:100]
                            self.on_tool_activity("tool_done", tc.name, preview)
                        break
                else:
                    self.on_tool_activity("tool_done", tc.name, "")

        return tool_results

    def _append_tool_results(
        self, tool_calls: list[ToolCallEvent], tool_results: str
    ) -> None:
        """Add tool results to conversation in the appropriate format."""
        if self._is_native:
            for tc in tool_calls:
                tool_call_id = tc.args.get("_tool_call_id", "")
                result_text = ""
                for r in tool_results.split("\n\n") if tool_results else []:
                    if r.startswith(f"[{tc.name}]"):
                        result_text = r
                        break
                if not result_text:
                    result_text = tool_results or "(no output)"
                if tool_call_id:
                    self.conversation.append(
                        "tool",
                        result_text,
                        tool_call_id=tool_call_id,
                        name=tc.name,
                    )
        else:
            if tool_results:
                self.conversation.append("user", tool_results)

    def _build_result(
        self, output_parts: list[str], tools_used: list[str]
    ) -> SubAgentResult:
        """Build the final SubAgentResult, saving conversation if possible."""
        final_output = "".join(output_parts).strip()

        if self._session_store:
            try:
                self._session_store.save_subagent(
                    parent=self._parent_name,
                    name=self.config.name,
                    run=self._run_index,
                    meta={
                        "task": (
                            self.conversation.to_messages()[1].get("content", "")
                            if len(self.conversation.to_messages()) > 1
                            else ""
                        ),
                        "turns": self._turns,
                        "tools_used": tools_used,
                        "success": True,
                        "duration": self._calculate_duration(),
                        "output_preview": final_output[:500],
                    },
                    conv_json=self.conversation.to_json(),
                )
            except Exception as e:
                logger.debug(
                    "Failed to save sub-agent conversation",
                    subagent=self.config.name,
                    error=str(e),
                )

        return SubAgentResult(
            output=final_output,
            success=True,
            turns=self._turns,
            duration=self._calculate_duration(),
            total_tokens=self._total_tokens,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            cached_tokens=self._cached_tokens,
            metadata={"tools_used": tools_used},
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tools(self, tool_calls: list[ToolCallEvent]) -> str:
        """Execute tool calls and return formatted results."""
        results: list[str] = []

        for tool_call in tool_calls:
            tool = self.registry.get_tool(tool_call.name)
            if tool is None:
                logger.warning(
                    "Sub-agent tool not available",
                    subagent_name=self.config.name,
                    tool_name=tool_call.name,
                )
                results.append(f"[{tool_call.name}] Error: Tool not available")
                continue

            args_preview = str(tool_call.args)[:100]
            logger.debug(
                "Sub-agent tool start",
                subagent_name=self.config.name,
                tool_name=tool_call.name,
                tool_args=args_preview,
            )

            try:
                context = (
                    self._build_tool_context() if self._build_tool_context else None
                )
                result = await tool.execute(tool_call.args, context=context)
                # Guard against tools that return str instead of ToolResult
                if isinstance(result, str):
                    results.append(f"[{tool_call.name}]\n{result}")
                    continue
                if result.success:
                    text_output = result.get_text_output()
                    output = text_output if text_output else "(no output)"
                    results.append(f"[{tool_call.name}]\n{output}")
                    logger.debug(
                        "Sub-agent tool success",
                        subagent_name=self.config.name,
                        tool_name=tool_call.name,
                        output_preview=(text_output or "")[:100].replace("\n", " "),
                    )
                else:
                    error = result.error or "Unknown error"
                    results.append(f"[{tool_call.name}] Error: {error}")
                    logger.warning(
                        "Sub-agent tool failed",
                        subagent_name=self.config.name,
                        tool_name=tool_call.name,
                        error=error,
                    )
            except Exception as e:
                results.append(f"[{tool_call.name}] Error: {str(e)}")
                logger.error(
                    "Sub-agent tool exception",
                    subagent_name=self.config.name,
                    tool_name=tool_call.name,
                    error=str(e),
                )

        return "\n\n".join(results)

    def _calculate_duration(self) -> float:
        """Calculate elapsed time."""
        if self._start_time:
            return (datetime.now() - self._start_time).total_seconds()
        return 0.0

    def _charge_budget_or_fail(self, tools_used: list[str]) -> SubAgentResult | None:
        """Consume one unit of the shared budget. Return a failed result
        when the budget is drained, or ``None`` when the caller may proceed.
        """
        budget = self.iteration_budget
        if budget is None:
            return None
        try:
            budget.consume(1)
            return None
        except BudgetExhausted as exc:
            logger.info(
                "Sub-agent hit shared iteration budget",
                subagent_name=self.config.name,
                turn=self._turns,
                remaining=budget.remaining,
                total=budget.total,
            )
            return SubAgentResult(
                success=False,
                error=f"BudgetExhausted: {exc}",
                turns=self._turns,
                duration=self._calculate_duration(),
                total_tokens=self._total_tokens,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                cached_tokens=self._cached_tokens,
                metadata={
                    "tools_used": tools_used,
                    "budget_exhausted": True,
                    "budget": budget.snapshot(),
                },
            )

    def cancel(self) -> None:
        """Request cancellation. Checked during LLM streaming and between turns."""
        self._cancelled = True
        self._running = False
        logger.info("Sub-agent cancel requested", subagent_name=self.config.name)

    @property
    def is_running(self) -> bool:
        """Check if sub-agent is currently running."""
        return self._running
