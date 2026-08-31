"""Run nested agents with isolated conversations and limited tool access."""

import asyncio
from datetime import datetime
from typing import Any

from kohakuterrarium.core.budget import (
    BudgetExhausted,
    IterationBudget,
)
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.tool_output import (
    discard_raw_output_file,
    normalize_tool_output,
)
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.result import (  # noqa: F401
    SUBAGENT_FRAMEWORK_HINTS,
    SubAgentJob,
    SubAgentResult,
    build_subagent_framework_hints,
)
from kohakuterrarium.modules.tool.base import BaseTool
from kohakuterrarium.parsing import ParserConfig, StreamParser, TextEvent, ToolCallEvent
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
from kohakuterrarium.prompt.aggregator import aggregate_system_prompt
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SubAgent:
    """Run a nested controller whose tools and output routing are constrained."""

    def __init__(
        self,
        config: SubAgentConfig,
        parent_registry: Registry,
        llm: LLMProvider,
        agent_path: Any = None,
        tool_format: str | None = None,
        plugin_manager: PluginManager | None = None,
        compact_manager: Any = None,
    ):
        self.config = config
        self.parent_registry = parent_registry
        self.llm = llm
        self.agent_path = agent_path
        self.tool_format = tool_format
        self.plugins = plugin_manager
        self._plugin_manager = plugin_manager
        self.compact_manager = compact_manager
        if self.compact_manager is not None:
            self.compact_manager._controller = self
            self.compact_manager._llm = self.llm
            self.compact_manager._agent_name = config.name

        self.on_tool_activity: Any = None

        self._build_tool_context: Any = None

        self._session_store: Any = None
        self._parent_name: str = ""
        self._run_index: int = 0
        self._job_id: str = ""

        # A shared budget coordinates parent and child iteration limits when configured.
        self.iteration_budget: IterationBudget | None = None

        self.registry = self._create_limited_registry()

        self.executor = Executor()
        for tool_name in self.registry.list_tools():
            tool = self.registry.get_tool(tool_name)
            if tool:
                self.executor.register_tool(tool)

        self.conversation = Conversation()

        # Messages are admitted between turns without changing task completion semantics.
        self._inbox: asyncio.Queue = asyncio.Queue()

        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cached_tokens = 0

        self._is_native = tool_format == "native"
        parser_tool_format = self._resolve_parser_format(tool_format)

        self._parser_config = ParserConfig(
            known_tools=set(self.registry.list_tools()),
            tool_format=parser_tool_format,
        )
        self._parser = StreamParser(self._parser_config)

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

    def _apply_tool_visibility(self, schemas: Any) -> Any:
        """Apply plugin tool-visibility restrictions to native schemas."""
        if self.plugins is None:
            return schemas
        visibility = self.plugins.collect_tool_visibility()
        if visibility is None or visibility.allowed_tools is None:
            return schemas
        return [schema for schema in schemas if schema.name in visibility.allowed_tools]

    def _build_system_prompt(self) -> str:
        """Build complete system prompt through the shared aggregator."""
        base_prompt = self.config.load_prompt(self.agent_path)
        plugin_ctx = PluginContext(
            agent_name=self.config.name,
            working_dir=self.agent_path,
            model=getattr(self.llm, "model", ""),
            _host_agent=self,
        )
        result = aggregate_system_prompt(
            base_prompt=base_prompt,
            registry=self.registry,
            include_tools=True,
            include_hints=True,
            skill_mode="dynamic",
            tool_format=self.tool_format or "bracket",
            runtime_plugins=self._plugin_manager,
            plugin_context=plugin_ctx,
        )
        if self._missing_tools:
            missing_note = (
                "## Unavailable Tools\n\n"
                "The following tools were requested but are not available: "
                + ", ".join(f"`{t}`" for t in self._missing_tools)
                + "\nDo NOT attempt to call these tools. Work with what is available."
            )
            result = f"{result}\n\n{missing_note}"

        logger.info(
            "Sub-agent system prompt built",
            subagent_name=self.config.name,
            tool_count=len(self.registry.list_tools()),
            prompt_length=len(result),
        )
        return result

    async def run(self, task: str) -> SubAgentResult:
        """Execute the sub-agent with a task."""
        self._running = True
        self._cancelled = False
        self._start_time = datetime.now()
        self._turns = 0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cached_tokens = 0

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
                total_tokens=self._total_tokens,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                cached_tokens=self._cached_tokens,
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
                total_tokens=self._total_tokens,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                cached_tokens=self._cached_tokens,
            )
        finally:
            self._running = False

    async def _run_internal(self, task: str) -> SubAgentResult:
        """Internal run logic. Runs conversation loop with tool execution."""
        self._setup_conversation(task)

        native_tool_schemas = None
        if self._is_native:
            native_tool_schemas = self._apply_tool_visibility(
                build_tool_schemas(self.registry)
            )

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
            # Admit live messages before spending the next model call.
            self._drain_inbox_into_conversation()
            # Budget exhaustion must be visible to the parent as a failed result.
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

            if tool_calls:
                tools_used.extend(tc.name for tc in tool_calls)
                tool_results = await self._execute_and_report_tools(tool_calls)
                self._append_tool_results(tool_calls, tool_results)
                continue

            # A message arriving during the turn requires one more response cycle.
            if self._inbox_has_pending():
                continue
            logger.info(
                "Sub-agent no tools called, finishing",
                subagent_name=self.config.name,
            )
            break

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
        """Run one model turn and return tool calls with emitted text parts."""
        messages = self.conversation.to_messages()
        if self.plugins:
            messages = await self.plugins.run_pre_hooks(
                "pre_llm_call",
                messages,
                model=getattr(self.llm, "model", ""),
                tools=native_tool_schemas if self._is_native else None,
            )
        if self._is_native and native_tool_schemas:
            tool_calls, output = await self._run_native_turn(
                messages,
                native_tool_schemas,
            )
        else:
            tool_calls, output = await self._run_text_turn(messages)
        output = await self._run_post_llm_plugins(messages, output)
        return tool_calls, output

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
                extra_fields=getattr(self.llm, "last_assistant_extra_fields", {}) or {},
            )
        else:
            self.conversation.append(
                "assistant",
                assistant_content,
                extra_fields=getattr(self.llm, "last_assistant_extra_fields", {}) or {},
            )

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

        self.conversation.append(
            "assistant",
            assistant_content,
            extra_fields=getattr(self.llm, "last_assistant_extra_fields", {}) or {},
        )
        self._log_turn_preview(assistant_content)
        self._accumulate_tokens()
        return tool_calls, output_parts

    async def _run_post_llm_plugins(
        self,
        messages: list[dict],
        output_parts: list[str],
    ) -> list[str]:
        """Run sub-agent post_llm_call hooks after the assistant message lands."""
        if not self.plugins:
            return output_parts
        current = "".join(output_parts)
        usage = getattr(self.llm, "last_usage", {}) or {}
        base_method = getattr(BasePlugin, "post_llm_call", None)
        for plugin in self.plugins._applicable_plugins():
            method = getattr(type(plugin), "post_llm_call", None)
            if method is None or method is base_method:
                continue
            try:
                rewritten = await plugin.post_llm_call(
                    messages,
                    current,
                    usage,
                    model=getattr(self.llm, "model", ""),
                )
            except Exception as exc:
                logger.warning(
                    "Sub-agent post_llm_call failed",
                    plugin_name=getattr(plugin, "name", "?"),
                    error=str(exc),
                    exc_info=True,
                )
                continue
            if isinstance(rewritten, str) and rewritten != current:
                current = rewritten
                last = self.conversation.get_last_assistant_message()
                if last is not None:
                    last.content = current
        return [current] if current else []

    def _accumulate_tokens(self) -> None:
        """Accumulate token usage from the last LLM call."""
        usage = getattr(self.llm, "last_usage", None)
        if usage and isinstance(usage, dict):
            self._prompt_tokens += usage.get("prompt_tokens", 0)
            self._completion_tokens += usage.get("completion_tokens", 0)
            self._total_tokens += usage.get("total_tokens", 0)
            self._cached_tokens += usage.get("cached_tokens", 0)
        # Compaction remains opt-in; token totals are still reported to the parent.
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

    async def _execute_and_report_tools(
        self, tool_calls: list[ToolCallEvent]
    ) -> list[str]:
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

        if self.on_tool_activity:
            # Results are positional: attribute each preview to its own call so
            # several calls to the same tool don't all report the first block.
            for tc, result in zip(tool_calls, tool_results):
                prefix = f"[{tc.name}]"
                if result.startswith(prefix):
                    if result.startswith(f"{prefix} Error:"):
                        error_msg = result.split("Error:", 1)[-1].strip()[:100]
                        self.on_tool_activity("tool_error", tc.name, error_msg)
                    else:
                        preview = result[len(prefix) :].strip()[:100]
                        self.on_tool_activity("tool_done", tc.name, preview)
                else:
                    self.on_tool_activity("tool_done", tc.name, "")

        return tool_results

    def _append_tool_results(
        self, tool_calls: list[ToolCallEvent], tool_results: list[str]
    ) -> None:
        """Add tool results to conversation in the appropriate format."""
        if self._is_native:
            for tc, result_text in zip(tool_calls, tool_results):
                tool_call_id = tc.args.get("_tool_call_id", "")
                if not result_text:
                    result_text = "(no output)"
                if tool_call_id:
                    self.conversation.append(
                        "tool",
                        result_text,
                        tool_call_id=tool_call_id,
                        name=tc.name,
                    )
        else:
            if tool_results:
                self.conversation.append("user", "\n\n".join(tool_results))

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
                        "job_id": self._job_id,
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
                logger.warning(
                    "Failed to save sub-agent conversation",
                    subagent=self.config.name,
                    error=str(e),
                    exc_info=True,
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

    async def _execute_tools(self, tool_calls: list[ToolCallEvent]) -> list[str]:
        """Execute tool calls and return one formatted result per call."""
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
                # Per-call wrapping prevents child hooks from leaking onto shared tools.
                exec_fn = tool.execute
                if self.plugins is not None:
                    exec_fn = self.plugins.wrap_method(
                        "pre_tool_execute",
                        "post_tool_execute",
                        tool.execute,
                        input_kwarg="args",
                        extra_kwargs={"tool_name": tool_call.name},
                    )
                try:
                    result = await exec_fn(tool_call.args, context=context)
                except PluginBlockError as block:
                    results.append(f"[{tool_call.name}] Error: {str(block)}")
                    logger.info(
                        "Sub-agent tool blocked by plugin",
                        subagent_name=self.config.name,
                        tool_name=tool_call.name,
                    )
                    continue
                # Preserve compatibility with tools predating the ToolResult contract.
                if isinstance(result, str):
                    max_output = (
                        tool.config.max_output if isinstance(tool, BaseTool) else 0
                    )
                    normalized = normalize_tool_output(
                        result,
                        max_output=max_output,
                        tool_name=tool_call.name,
                    )
                    results.append(f"[{tool_call.name}]\n{normalized.text}")
                    continue
                max_output = tool.config.max_output if isinstance(tool, BaseTool) else 0
                result_metadata = (
                    result.metadata if isinstance(result.metadata, dict) else {}
                )
                normalized = normalize_tool_output(
                    result.output,
                    max_output=max_output,
                    tool_name=tool_call.name,
                    saved_to=result_metadata.get("raw_output_path"),
                )
                if tool_call.name == "bash" and (
                    not result.success or not normalized.metadata.get("truncated")
                ):
                    discard_raw_output_file(result_metadata)
                if result.success:
                    text_output = normalized.text
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

        return results

    def _calculate_duration(self) -> float:
        """Calculate elapsed time."""
        if self._start_time:
            return (datetime.now() - self._start_time).total_seconds()
        return 0.0

    def _build_partial_result(
        self,
        error: str,
        *,
        interrupted: bool = False,
        cancelled: bool = False,
        tools_used: list[str] | None = None,
    ) -> SubAgentResult:
        """Build a failed result without dropping tokens spent so far."""
        return SubAgentResult(
            success=False,
            error=error,
            interrupted=interrupted,
            cancelled=cancelled,
            turns=self._turns,
            duration=self._calculate_duration(),
            total_tokens=self._total_tokens,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            cached_tokens=self._cached_tokens,
            metadata={"tools_used": tools_used or []},
        )

    def _charge_budget_or_fail(self, tools_used: list[str]) -> SubAgentResult | None:
        """Consume one shared iteration or return a budget-exhausted result."""
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

    def push_message(self, content: str) -> None:
        """Queue a live user message without changing task completion semantics."""
        if content:
            self._inbox.put_nowait({"role": "user", "content": content})

    def _drain_inbox_into_conversation(self) -> int:
        """Append queued inbox messages as user turns and return their count."""
        count = 0
        while True:
            try:
                message = self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            content = (
                message.get("content", "")
                if isinstance(message, dict)
                else str(message)
            )
            if content:
                self.conversation.append("user", content)
                count += 1
        return count

    def _inbox_has_pending(self) -> bool:
        return not self._inbox.empty()

    def cancel(self) -> None:
        """Request cancellation. Checked during LLM streaming and between turns."""
        self._cancelled = True
        self._running = False
        logger.info("Sub-agent cancel requested", subagent_name=self.config.name)

    @property
    def is_running(self) -> bool:
        """Check if sub-agent is currently running."""
        return self._running
