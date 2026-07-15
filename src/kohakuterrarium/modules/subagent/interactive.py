"""Run long-lived sub-agents that react to parent context updates."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.modules.subagent.base import SubAgent, SubAgentResult
from kohakuterrarium.modules.subagent.config import ContextUpdateMode, SubAgentConfig
from kohakuterrarium.parsing import StreamParser, TextEvent, ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ContextUpdate:
    """Timestamp one context update for an interactive sub-agent."""

    context: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class InteractiveOutput:
    """Represent a streamed or completed interactive output chunk."""

    text: str
    is_complete: bool = False
    context: dict[str, Any] = field(default_factory=dict)


class InteractiveSubAgent(SubAgent):
    """Keep a sub-agent alive and process updates under a configurable mode."""

    def __init__(
        self,
        config: SubAgentConfig,
        parent_registry: Registry,
        llm: LLMProvider,
        agent_path: Any = None,
        tool_format: str | None = None,
    ):
        super().__init__(config, parent_registry, llm, agent_path, tool_format)

        self._active = False
        self._current_task: asyncio.Task | None = None

        # Async primitives are created only after an event loop is running.
        self._context_queue: asyncio.Queue[ContextUpdate] | None = None
        self._stop_event: asyncio.Event | None = None
        self._generation_lock: asyncio.Lock | None = None

        self._current_context: dict[str, Any] = {}

        self.on_output: Callable[[InteractiveOutput], None] | None = None

        self._output_buffer: list[str] = []

        logger.debug(
            "InteractiveSubAgent created",
            agent_name=config.name,
            context_mode=config.context_mode.value,
        )

    @property
    def is_active(self) -> bool:
        """Check if agent is active and processing."""
        return self._active

    async def start(self) -> None:
        """Start listening for context updates and initialize async primitives."""
        if self._active:
            logger.warning(
                "InteractiveSubAgent already active", agent_name=self.config.name
            )
            return

        self._context_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._generation_lock = asyncio.Lock()

        self._active = True
        self._stop_event.clear()

        self.conversation = Conversation()
        system_prompt = self.config.load_prompt(self.agent_path)
        self.conversation.append("system", system_prompt)

        self._current_task = asyncio.create_task(self._run_loop())

        logger.info("InteractiveSubAgent started", agent_name=self.config.name)

    async def stop(self) -> None:
        """Cancel the current generation and stop listening for updates."""
        if not self._active:
            return

        self._active = False
        if self._stop_event is not None:
            self._stop_event.set()

        if self._current_task:
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass
            self._current_task = None

        logger.info("InteractiveSubAgent stopped", agent_name=self.config.name)

    async def push_context(self, context: dict[str, Any]) -> None:
        """Apply a context update according to the configured update mode."""
        if not self._active:
            logger.warning(
                "Cannot push context to inactive agent", agent_name=self.config.name
            )
            return

        update = ContextUpdate(context=context)

        match self.config.context_mode:
            case ContextUpdateMode.INTERRUPT_RESTART:
                await self._handle_interrupt_restart(update)
            case ContextUpdateMode.QUEUE_APPEND:
                await self._handle_queue_append(update)
            case ContextUpdateMode.FLUSH_REPLACE:
                await self._handle_flush_replace(update)

    async def _handle_interrupt_restart(self, update: ContextUpdate) -> None:
        """Interrupt current generation and restart with new context."""
        logger.debug(
            "Interrupt-restart context update",
            agent_name=self.config.name,
            context_keys=list(update.context.keys()),
        )

        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        self._output_buffer.clear()

        self._current_context = update.context.copy()
        self._current_task = asyncio.create_task(
            self._generate_response(update.context)
        )

    async def _handle_queue_append(self, update: ContextUpdate) -> None:
        """Queue the update for processing after current generation."""
        logger.debug(
            "Queue-append context update",
            agent_name=self.config.name,
            queue_size=self._context_queue.qsize(),
        )
        await self._context_queue.put(update)

    async def _handle_flush_replace(self, update: ContextUpdate) -> None:
        """Flush current output and replace context immediately."""
        logger.debug(
            "Flush-replace context update",
            agent_name=self.config.name,
            context_keys=list(update.context.keys()),
        )

        if self._output_buffer:
            output = InteractiveOutput(
                text="".join(self._output_buffer),
                is_complete=True,
                context=self._current_context.copy(),
            )
            self._emit_output(output)
            self._output_buffer.clear()

        self._current_context = update.context.copy()
        await self._context_queue.put(update)

    async def _run_loop(self) -> None:
        """Main event loop for interactive sub-agent."""
        try:
            while self._active and not self._stop_event.is_set():
                try:
                    update = await asyncio.wait_for(
                        self._context_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                # Both queued modes must generate after any prior flush is complete.
                if self.config.context_mode in (
                    ContextUpdateMode.QUEUE_APPEND,
                    ContextUpdateMode.FLUSH_REPLACE,
                ):
                    await self._generate_response(update.context)

        except asyncio.CancelledError:
            logger.debug("Interactive loop cancelled", agent_name=self.config.name)
            raise

    async def _generate_response(self, context: dict[str, Any]) -> SubAgentResult:
        """Generate and stream one response for the supplied context."""
        async with self._generation_lock:
            self._turns = 0
            self._start_time = datetime.now()
            output_parts: list[str] = []

            try:
                user_message = self._format_context_as_message(context)
                self.conversation.append("user", user_message)

                while self._turns < self.config.max_turns:
                    self._turns += 1

                    messages = self.conversation.to_messages()
                    assistant_content = ""
                    self._parser = StreamParser(self._parser_config)

                    tool_calls: list[ToolCallEvent] = []

                    async for chunk in self.llm.chat(messages, stream=True):
                        if not self._active:
                            raise asyncio.CancelledError()

                        assistant_content += chunk

                        for event in self._parser.feed(chunk):
                            if isinstance(event, ToolCallEvent):
                                tool_calls.append(event)
                            elif isinstance(event, TextEvent):
                                output_parts.append(event.text)
                                self._output_buffer.append(event.text)

                                chunk_output = InteractiveOutput(
                                    text=event.text,
                                    is_complete=False,
                                    context=context,
                                )
                                self._emit_output(chunk_output)

                    for event in self._parser.flush():
                        if isinstance(event, ToolCallEvent):
                            tool_calls.append(event)
                        elif isinstance(event, TextEvent):
                            output_parts.append(event.text)
                            self._output_buffer.append(event.text)

                    self.conversation.append("assistant", assistant_content)

                    if not tool_calls:
                        break

                    tool_results = await self._execute_tools(tool_calls)
                    if tool_results:
                        self.conversation.append("user", tool_results)

                final_output = "".join(output_parts).strip()
                complete_output = InteractiveOutput(
                    text="",
                    is_complete=True,
                    context=context,
                )
                self._emit_output(complete_output)

                return SubAgentResult(
                    output=final_output,
                    success=True,
                    turns=self._turns,
                    duration=self._calculate_duration(),
                )

            except asyncio.CancelledError:
                logger.debug(
                    "Generation cancelled",
                    agent_name=self.config.name,
                    turns=self._turns,
                )
                return SubAgentResult(
                    output="".join(output_parts),
                    success=False,
                    error="Cancelled",
                    turns=self._turns,
                    duration=self._calculate_duration(),
                )

    def _format_context_as_message(self, context: dict[str, Any]) -> str:
        """Format context dict as a user message."""
        if "message" in context:
            return str(context["message"])
        if "input" in context:
            return str(context["input"])
        if "text" in context:
            return str(context["text"])

        parts = []
        for key, value in context.items():
            parts.append(f"{key}: {value}")
        return "\n".join(parts)

    def _emit_output(self, output: InteractiveOutput) -> None:
        """Emit output via callback."""
        if self.on_output:
            try:
                self.on_output(output)
            except Exception as e:
                logger.error(
                    "Output callback error",
                    agent_name=self.config.name,
                    error=str(e),
                )

    def get_buffered_output(self) -> str:
        """Return and clear output accumulated for parent context."""
        output = "".join(self._output_buffer)
        self._output_buffer.clear()
        return output

    def clear_conversation(self) -> None:
        """Clear conversation history while retaining the system prompt."""
        self.conversation.clear(keep_system=True)

        logger.debug("Conversation cleared", agent_name=self.config.name)
