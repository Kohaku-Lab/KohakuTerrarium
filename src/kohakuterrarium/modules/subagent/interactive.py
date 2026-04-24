"""
Interactive sub-agent - stays alive and receives context updates.

Unlike regular sub-agents that complete after a task, interactive sub-agents:
- Stay running continuously
- Receive context updates from parent controller
- Handle context updates based on configured mode
- Can stream output externally or return to parent as context
"""

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
    """A context update for interactive sub-agent."""

    context: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class InteractiveOutput:
    """Output chunk from interactive sub-agent."""

    text: str
    is_complete: bool = False
    context: dict[str, Any] = field(default_factory=dict)


class InteractiveSubAgent(SubAgent):
    """
    Interactive sub-agent that stays alive and handles context updates.

    Unlike regular SubAgent, this:
    - Runs continuously until explicitly stopped
    - Receives context updates from parent controller
    - Handles updates based on context_mode configuration
    - Can stream output to external output module

    Usage:
        config = SubAgentConfig(
            name="output",
            interactive=True,
            context_mode=ContextUpdateMode.INTERRUPT_RESTART,
            output_to=OutputTarget.EXTERNAL,
        )
        agent = InteractiveSubAgent(config, registry, llm)

        # Start the agent
        await agent.start()

        # Push context updates
        await agent.push_context({"user_input": "Hello!"})

        # Receive output via callback
        agent.on_output = lambda chunk: print(chunk.text)

        # Stop when done
        await agent.stop()
    """

    def __init__(
        self,
        config: SubAgentConfig,
        parent_registry: Registry,
        llm: LLMProvider,
        agent_path: Any = None,
        tool_format: str | None = None,
    ):
        super().__init__(config, parent_registry, llm, agent_path, tool_format)

        # Interactive state
        self._active = False
        self._current_task: asyncio.Task | None = None

        # Asyncio primitives - initialized lazily in start() to avoid
        # creating them outside an async context (no running event loop)
        self._context_queue: asyncio.Queue[ContextUpdate] | None = None
        self._stop_event: asyncio.Event | None = None
        self._generation_lock: asyncio.Lock | None = None

        # Current context being processed
        self._current_context: dict[str, Any] = {}

        # Output callback
        self.on_output: Callable[[InteractiveOutput], None] | None = None

        # For return_as_context - collected output to return to parent
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
        """
        Start the interactive sub-agent.

        Begins listening for context updates.
        Creates asyncio primitives here (inside async context).
        """
        if self._active:
            logger.warning(
                "InteractiveSubAgent already active", agent_name=self.config.name
            )
            return

        # Create asyncio primitives inside async context
        self._context_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._generation_lock = asyncio.Lock()

        self._active = True
        self._stop_event.clear()

        # Initialize conversation with system prompt
        self.conversation = Conversation()
        system_prompt = self.config.load_prompt(self.agent_path)
        self.conversation.append("system", system_prompt)

        # Start the main loop
        self._current_task = asyncio.create_task(self._run_loop())

        logger.info("InteractiveSubAgent started", agent_name=self.config.name)

    async def stop(self) -> None:
        """
        Stop the interactive sub-agent.

        Cancels any current generation and stops listening.
        """
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
        """
        Push a context update to the sub-agent.

        How the update is handled depends on context_mode:
        - INTERRUPT_RESTART: Cancel current, start new with this context
        - QUEUE_APPEND: Add to queue, process after current completes
        - FLUSH_REPLACE: Flush current output, replace context immediately

        Args:
            context: New context data
        """
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

        # Cancel any current generation
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        # Clear output buffer for new generation
        self._output_buffer.clear()

        # Update context and restart
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

        # Emit any buffered output as complete
        if self._output_buffer:
            output = InteractiveOutput(
                text="".join(self._output_buffer),
                is_complete=True,
                context=self._current_context.copy(),
            )
            self._emit_output(output)
            self._output_buffer.clear()

        # Update context for next iteration
        self._current_context = update.context.copy()
        await self._context_queue.put(update)

    async def _run_loop(self) -> None:
        """Main event loop for interactive sub-agent."""
        try:
            while self._active and not self._stop_event.is_set():
                # Wait for context update
                try:
                    update = await asyncio.wait_for(
                        self._context_queue.get(),
                        timeout=0.1,  # Short timeout to check stop condition
                    )
                except asyncio.TimeoutError:
                    continue

                # Process the update
                if self.config.context_mode == ContextUpdateMode.QUEUE_APPEND:
                    # For queue mode, process sequentially
                    await self._generate_response(update.context)

        except asyncio.CancelledError:
            logger.debug("Interactive loop cancelled", agent_name=self.config.name)
            raise

    async def _generate_response(self, context: dict[str, Any]) -> SubAgentResult:
        """
        Generate a response for the given context.

        Args:
            context: Context data for generation

        Returns:
            SubAgentResult with generated output
        """
        async with self._generation_lock:
            self._turns = 0
            self._start_time = datetime.now()
            output_parts: list[str] = []

            try:
                # Build user message from context
                user_message = self._format_context_as_message(context)
                self.conversation.append("user", user_message)

                # Run conversation loop
                while self._turns < self.config.max_turns:
                    self._turns += 1

                    messages = self.conversation.to_messages()
                    assistant_content = ""
                    self._parser = StreamParser(self._parser_config)

                    tool_calls: list[ToolCallEvent] = []

                    # Stream response
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

                                # Emit streaming output
                                chunk_output = InteractiveOutput(
                                    text=event.text,
                                    is_complete=False,
                                    context=context,
                                )
                                self._emit_output(chunk_output)

                    # Flush parser
                    for event in self._parser.flush():
                        if isinstance(event, ToolCallEvent):
                            tool_calls.append(event)
                        elif isinstance(event, TextEvent):
                            output_parts.append(event.text)
                            self._output_buffer.append(event.text)

                    self.conversation.append("assistant", assistant_content)

                    # No more tool calls = generation complete
                    if not tool_calls:
                        break

                    # Execute tools
                    tool_results = await self._execute_tools(tool_calls)
                    if tool_results:
                        self.conversation.append("user", tool_results)

                # Emit completion
                final_output = "".join(output_parts).strip()
                complete_output = InteractiveOutput(
                    text="",  # Empty since we already streamed
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
        # Look for common context keys
        if "message" in context:
            return str(context["message"])
        if "input" in context:
            return str(context["input"])
        if "text" in context:
            return str(context["text"])

        # Format as key-value pairs
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
        """
        Get and clear the output buffer.

        Used for return_as_context functionality.

        Returns:
            Accumulated output text
        """
        output = "".join(self._output_buffer)
        self._output_buffer.clear()
        return output

    def clear_conversation(self) -> None:
        """
        Clear conversation history.

        Keeps system prompt but removes all user/assistant messages.
        Useful for sliding window context management.
        """
        self.conversation.clear(keep_system=True)

        logger.debug("Conversation cleared", agent_name=self.config.name)
