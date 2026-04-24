"""
Controller - Main LLM conversation loop with event queue.

The controller orchestrates agent operation:
- Receives TriggerEvents (input, tool completion, etc.)
- Maintains conversation context
- Runs LLM and parses output
- Dispatches tool calls and sub-agents

Supports multimodal content (text + images).
"""

import asyncio
import base64
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from kohakuterrarium.llm.base import ToolSchema

from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.commands.base import Command, CommandResult
from kohakuterrarium.commands.read import (
    InfoCommand,
    JobsCommand,
    ReadCommand,
    WaitCommand,
)
from kohakuterrarium.core.controller_plugins import (
    register_controller_command,
    run_post_llm_call_chain,
)
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.job import JobResult, JobStatus, JobStore
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.message import ContentPart, FilePart, ImagePart, TextPart
from kohakuterrarium.llm.tools import build_provider_native_tools, build_tool_schemas
from kohakuterrarium.modules.tool.base import ToolInfo
from kohakuterrarium.parsing import (
    AssistantImageEvent,
    CommandEvent,
    CommandResultEvent,
    ParseEvent,
    ParserConfig,
    StreamParser,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
_FILE_PLACEHOLDER_RE = re.compile(r"\[\[file:(?P<ref>[^\]]+)\]\]")


def _merge_text_and_parts(
    text: str, structured_parts: list[ContentPart]
) -> str | list[ContentPart]:
    """Combine streamed text with structured parts (text-first list)."""
    if not structured_parts:
        return text
    merged: list[ContentPart] = []
    if text:
        merged.append(TextPart(text=text))
    merged.extend(structured_parts)
    return merged


@dataclass
class ControllerConfig:
    """
    Configuration for the controller.

    Attributes:
        system_prompt: Base system prompt
        include_job_status: Include job status in context
        include_tools_list: Include tool list in system prompt
        batch_stackable_events: Batch stackable events together
        max_messages: Maximum number of messages to keep
        ephemeral: If True, clear conversation after each interaction (keep system only)
        known_outputs: Set of known output target names (e.g., "discord")
        tool_format: Tool calling format — "bracket", "xml", "native", or None
    """

    system_prompt: str = "You are a helpful assistant."
    include_job_status: bool = True
    include_tools_list: bool = True
    batch_stackable_events: bool = True
    max_messages: int = 50  # Keep last 50 messages
    ephemeral: bool = False  # Clear after each interaction (for group chat bots)
    known_outputs: set[str] = field(default_factory=set)  # Output targets for parser
    tool_format: str | None = None  # "bracket", "xml", "native", or None (auto)
    # Pre-LLM sanitiser (mirrors ``AgentConfig.sanitize_orphan_tool_calls``).
    # Threads through to ``ConversationConfig`` so the wire payload drops
    # compact-induced orphan tool_call / tool-result fragments.
    sanitize_orphan_tool_calls: bool = True


@dataclass
class ControllerContext:
    """Context passed to commands/handlers.

    ``skills_registry`` lets built-in skill/info handlers reach the runtime
    :class:`SkillRegistry`.
    """

    controller: "Controller"
    job_store: JobStore
    registry: Registry
    skills_registry: Any | None = None

    def get_job_status(self, job_id: str) -> JobStatus | None:
        """Get job status."""
        return self.job_store.get_status(job_id)

    def get_job_result(self, job_id: str) -> JobResult | None:
        """Get job result."""
        if self.controller.executor:
            return self.controller.executor.get_result(job_id)
        return self.job_store.get_result(job_id)

    def get_tool_info(self, tool_name: str) -> ToolInfo | None:
        """Get tool info."""
        return self.registry.get_tool_info(tool_name)

    def get_subagent_info(self, subagent_name: str) -> str | None:
        """Get sub-agent info (placeholder)."""
        return None


class Controller:
    """
    Main controller for agent operation.

    Manages:
    - Event queue for incoming triggers
    - Conversation history
    - LLM interaction with streaming
    - Tool/sub-agent dispatch
    - Command execution

    Usage:
        controller = Controller(llm_provider, config)

        # Push events
        await controller.push_event(trigger_event)

        # Run controller loop
        async for parse_event in controller.run_once():
            if isinstance(parse_event, TextEvent):
                print(parse_event.text, end="")
            elif isinstance(parse_event, ToolCallEvent):
                handle_tool_call(parse_event)
    """

    def __init__(
        self,
        llm: LLMProvider,
        config: ControllerConfig | None = None,
        executor: Executor | None = None,
        registry: Registry | None = None,
    ):
        """
        Initialize controller.

        Args:
            llm: LLM provider for chat
            config: Controller configuration
            executor: Tool executor (creates one if None)
            registry: Module registry (creates one if None)
        """
        self.llm = llm
        self.config = config or ControllerConfig()
        self.executor = executor
        self.registry = registry or Registry()

        # Conversation history (with limits from config)
        conv_config = ConversationConfig(
            max_messages=self.config.max_messages,
            keep_system=True,
            sanitize_orphan_tool_calls=self.config.sanitize_orphan_tool_calls,
        )
        self.conversation = Conversation(conv_config)

        # Token usage tracking
        self._last_usage: dict[str, int] = {}

        # Session store for artifact persistence. Attached by the
        # parent agent after construction via ``attach_session_store``
        # so the controller can write generated images (and any future
        # binary artifact) to disk alongside the session file.
        self.session_store: Any = None

        # Event queue
        self._event_queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        self._pending_events: list[TriggerEvent] = []

        # Stream parser (config built lazily from registry)
        self._parser_config: ParserConfig | None = None
        self._parser: StreamParser | None = None

        # Interrupt flag: checked during LLM streaming
        self._interrupted = False

        # Plugin manager (set by agent after creation, None = no overhead)
        self.plugins: Any = None

        # Output router (set by agent after creation). Used to emit
        # ``assistant_message_edited`` activity events when a plugin's
        # post_llm_call rewrites the response.
        self.output_router: Any = None

        # Messages queued by plugins via
        # ``PluginContext.inject_message_before_llm``. Drained right
        # before the ``pre_llm_call`` hooks run on the next LLM round,
        # so every plugin sees the injected messages in its ``messages``
        # argument.
        self._pending_injections: list[dict] = []

        # Job store (shared with executor if provided)
        if executor:
            self.job_store = executor.job_store
        else:
            self.job_store = JobStore()

        # Commands
        self._commands: dict[str, Command] = {
            "read_job": ReadCommand(),
            "info": InfoCommand(),
            "jobs": JobsCommand(),
            "wait": WaitCommand(),
        }

        # Context for commands
        self._context = ControllerContext(
            controller=self,
            job_store=self.job_store,
            registry=self.registry,
        )

        # Setup system prompt
        self._setup_system_prompt()

    def _get_parser(self) -> StreamParser:
        """Get parser with current registry tools, sub-agents, and outputs."""
        # Build config from current registry state
        known_tools = set(self.registry.list_tools())
        known_subagents = set(self.registry.list_subagents())

        # Resolve tool format for parser
        fmt = self.config.tool_format
        tool_format = BRACKET_FORMAT  # default
        if fmt == "xml":
            tool_format = XML_FORMAT

        self._parser_config = ParserConfig(
            known_tools=known_tools,
            known_subagents=known_subagents,
            known_outputs=self.config.known_outputs,
            tool_format=tool_format,
            known_commands=set(self._commands.keys()),
        )
        return StreamParser(self._parser_config)

    @property
    def _is_native_mode(self) -> bool:
        """Check if using native API tool calling."""
        return self.config.tool_format == "native"

    def _get_native_tool_schemas(self) -> "list[ToolSchema]":
        """Build native tool schemas from registry."""
        return build_tool_schemas(self.registry)

    def _get_provider_native_tools(self) -> list:
        """Collect tools the active provider should translate into
        wire-format built-in tool specs. See
        :func:`build_provider_native_tools`."""
        return build_provider_native_tools(self.registry)

    def _setup_system_prompt(self) -> None:
        """Setup initial system prompt."""
        prompt_parts = [self.config.system_prompt]

        # Add tool list
        if self.config.include_tools_list:
            tools_prompt = self.registry.get_tools_prompt()
            if tools_prompt:
                prompt_parts.append(tools_prompt)

        # Join and add to conversation
        full_prompt = "\n\n".join(prompt_parts)
        self.conversation.append("system", full_prompt)

    async def push_event(self, event: TriggerEvent) -> None:
        """
        Push an event to the controller queue.

        Args:
            event: Trigger event to process
        """
        await self._event_queue.put(event)
        logger.debug("Event pushed", event_type=event.type)

    def push_event_sync(self, event: TriggerEvent) -> None:
        """Push event synchronously (for callbacks)."""
        self._event_queue.put_nowait(event)

    async def _collect_events(self) -> list[TriggerEvent]:
        """Collect and batch pending events."""
        events: list[TriggerEvent] = []

        # First, use any pending events from previous run
        if self._pending_events:
            events.extend(self._pending_events)
            self._pending_events.clear()

        # Get first event from queue if we don't have any yet
        if not events:
            if self._event_queue.empty():
                # No events at all, will block until one arrives
                first = await self._event_queue.get()
                events.append(first)
            else:
                # Get first event non-blocking
                events.append(self._event_queue.get_nowait())

        # Collect additional stackable events (non-blocking)
        if self.config.batch_stackable_events:
            while not self._event_queue.empty():
                try:
                    event = self._event_queue.get_nowait()
                    if event.stackable and events and events[-1].stackable:
                        events.append(event)
                    else:
                        # Non-stackable, save for next run
                        self._pending_events.append(event)
                        break
                except asyncio.QueueEmpty:
                    break

        return events

    def _format_events_for_context(
        self, events: list[TriggerEvent]
    ) -> "str | list[ContentPart]":
        """
        Format events as user message content.

        Returns multimodal content if any event has images.
        """
        text_parts: list[str] = []
        image_parts: list[ImagePart] = []
        file_parts: list[FilePart] = []
        has_multimodal = False

        for event in events:
            if event.type == "user_input":
                if isinstance(event.content, list):
                    has_multimodal = True
                    # Extract text and non-text parts from multimodal content
                    for part in event.content:
                        if isinstance(part, TextPart):
                            text_parts.append(part.text)
                        elif isinstance(part, ImagePart):
                            image_parts.append(part)
                        elif isinstance(part, FilePart):
                            file_parts.append(part)
                elif isinstance(event.content, str):
                    text_parts.append(event.content)
            elif event.type == "tool_complete":
                content_text = event.get_text_content()
                text_parts.append(f"[Tool {event.job_id} completed]\n{content_text}")
            elif event.type == "subagent_output":
                content_text = event.get_text_content()
                text_parts.append(f"[Sub-agent {event.job_id} output]\n{content_text}")
            else:
                content_text = event.get_text_content()
                text_parts.append(f"[{event.type}] {content_text}")

        # Combine text
        combined_text = "\n\n".join(text_parts)

        # Return multimodal if we have non-text parts
        if has_multimodal and (image_parts or file_parts):
            result: list[ContentPart] = [TextPart(text=combined_text)]
            result.extend(image_parts)
            result.extend(file_parts)
            return result

        return combined_text

    def _build_turn_context(
        self, events: list[TriggerEvent]
    ) -> tuple[str | list[ContentPart], str]:
        """
        Build user message content from events plus job status.

        Combines job status context and event content (multimodal-aware)
        into final user content for the conversation.

        Returns:
            Tuple of (user_content, combined_text). combined_text is the
            text-only portion, used to detect empty messages in native mode.
        """
        text_context_parts: list[str] = []
        image_context_parts: list[ImagePart] = []
        file_context_parts: list[FilePart] = []

        if self.config.include_job_status:
            job_context = self.job_store.format_context()
            if job_context:
                text_context_parts.append(job_context)

        # Add event content (may be multimodal)
        event_content = self._format_events_for_context(events)

        if isinstance(event_content, str):
            text_context_parts.append(event_content)
        else:
            # Multimodal content: extract text and keep non-text parts
            for part in event_content:
                if isinstance(part, TextPart):
                    text_context_parts.append(part.text)
                elif isinstance(part, ImagePart):
                    image_context_parts.append(part)
                elif isinstance(part, FilePart):
                    file_context_parts.append(part)

        combined_text = "\n\n".join(text_context_parts)

        if image_context_parts or file_context_parts:
            user_content: str | list[ContentPart] = [TextPart(text=combined_text)]
            user_content.extend(image_context_parts)
            user_content.extend(file_context_parts)
        else:
            user_content = combined_text

        return user_content, combined_text

    async def _run_native_completion(
        self, messages: list[dict], tool_schemas: "list[ToolSchema]"
    ) -> AsyncIterator[ParseEvent]:
        """
        Run LLM in native tool-calling mode.

        Streams text chunks as TextEvents, extracts native tool calls,
        appends the assistant message (with tool_calls metadata) to
        conversation, and yields ToolCallEvent/SubAgentCallEvent for
        each native call.
        """
        assistant_content = ""

        provider_native_tools = self._get_provider_native_tools()

        async for chunk in self.llm.chat(
            messages,
            stream=True,
            tools=tool_schemas or None,
            provider_native_tools=provider_native_tools or None,
        ):
            if self._interrupted:
                break
            assistant_content += chunk
            if chunk:
                yield TextEvent(text=chunk)

        self._log_token_usage()

        # Structured assistant parts — e.g. images from a provider-
        # native image-generation tool. Persist them, rewrite URLs,
        # and emit a StreamEvent per image so the UI can render live.
        structured_parts = self._collect_structured_assistant_parts()
        for part in structured_parts:
            if isinstance(part, ImagePart):
                yield AssistantImageEvent(
                    url=part.url,
                    detail=part.detail,
                    source_type=part.source_type,
                    source_name=part.source_name,
                    revised_prompt=getattr(part, "revised_prompt", None),
                )

        # Extract native tool calls from LLM response
        native_calls = (
            self.llm.last_tool_calls if hasattr(self.llm, "last_tool_calls") else []
        )

        if native_calls:
            tool_calls_data = []
            known_subagents = set(self.registry.list_subagents())

            for tc in native_calls:
                tool_calls_data.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                    }
                )
                logger.info(
                    "Native tool call",
                    tool_name=tc.name,
                    tool_args=tc.arguments[:100],
                )
                call_args = {**tc.parsed_arguments(), "_tool_call_id": tc.id}
                if tc.name in known_subagents:
                    yield SubAgentCallEvent(
                        name=tc.name, args=call_args, raw=tc.arguments
                    )
                else:
                    yield ToolCallEvent(name=tc.name, args=call_args, raw=tc.arguments)

            # Append assistant message WITH tool_calls metadata
            final_content = _merge_text_and_parts(assistant_content, structured_parts)
            self.conversation.append(
                "assistant",
                final_content,
                tool_calls=tool_calls_data,
            )
        else:
            # No tool calls: normal assistant message
            final_content = _merge_text_and_parts(assistant_content, structured_parts)
            self.conversation.append("assistant", final_content)

    # ------------------------------------------------------------------
    # Structured assistant content (images etc. from provider-native tools)
    # ------------------------------------------------------------------

    _DATA_URL_RE = re.compile(r"^data:image/(?P<ext>[\w+-]+);base64,(?P<b64>.*)$")

    def _collect_structured_assistant_parts(self) -> list[ContentPart]:
        """Pull structured parts the provider captured during the turn.

        Providers surface these via
        ``last_assistant_content_parts``. For every ``ImagePart`` whose
        URL is a ``data:image/…`` payload, we decode the bytes, write
        them into the session's artifacts directory, and rewrite the
        URL to a served ``/api/sessions/{id}/artifacts/…`` path so the
        conversation JSON stays small and the frontend can stream the
        image lazily. Providers without structured output return
        ``None`` here and this is a no-op.
        """
        source = getattr(self.llm, "last_assistant_content_parts", None)
        if source is None:
            return []
        materialized: list[ContentPart] = []
        for part in source:
            if isinstance(part, ImagePart):
                materialized.append(self._persist_image_part(part))
            else:
                materialized.append(part)
        return materialized

    def _persist_image_part(self, part: ImagePart) -> ImagePart:
        """Persist a data-URL image part to disk, return a rewritten part.

        Falls back to the original ImagePart (with the data URL) if no
        session store is attached or the URL isn't a recognised data
        URL — keeping behavior correct in unit tests / ephemeral runs.
        """
        match = self._DATA_URL_RE.match(part.url or "")
        if not match:
            return part
        store = getattr(self, "session_store", None)
        if store is None:
            return part

        ext = match.group("ext").split(";", 1)[0].lower()
        b64 = match.group("b64")
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("Failed to decode assistant image", error=str(e))
            return part

        base_name = (part.source_name or f"img_{int(time.time() * 1000)}").strip()
        safe_stem = re.sub(r"[^\w.-]", "_", base_name) or "img"
        safe_ext = re.sub(r"[^\w]", "", ext) or "png"
        filename = f"generated_images/{safe_stem}.{safe_ext}"
        try:
            disk_path = store.write_artifact(filename, raw)
        except Exception as e:
            logger.warning(
                "Failed to persist assistant image — falling back to data URL",
                error=str(e),
            )
            return part

        session_id = getattr(store, "session_id", "") or ""
        if session_id:
            served = f"/api/sessions/{session_id}/artifacts/{filename}"
        else:
            served = disk_path.as_uri()

        new_part = ImagePart(
            url=served,
            detail=part.detail,
            source_type=part.source_type,
            source_name=part.source_name,
        )
        # Preserve opaque metadata (e.g. revised_prompt) for the event log.
        for attr in ("revised_prompt",):
            if hasattr(part, attr):
                setattr(new_part, attr, getattr(part, attr))
        return new_part

    async def _run_text_completion(
        self, messages: list[dict]
    ) -> AsyncIterator[ParseEvent]:
        """
        Run LLM in custom text format mode.

        Creates a stream parser, feeds chunks through it, handles
        CommandEvents inline (yielding CommandResultEvent), and yields
        all other ParseEvents. Flushes the parser at end of stream.

        After this generator completes, self._last_assistant_content
        holds the full assistant text for conversation append.
        """
        self._parser = self._get_parser()
        assistant_content = ""

        provider_native_tools = self._get_provider_native_tools()

        async for chunk in self.llm.chat(
            messages,
            stream=True,
            provider_native_tools=provider_native_tools or None,
        ):
            if self._interrupted:
                break
            assistant_content += chunk

            for event in self._parser.feed(chunk):
                if isinstance(event, CommandEvent):
                    text, result_event = await self._execute_command_inline(event)
                    assistant_content += text
                    yield result_event
                else:
                    yield event

        # Flush remaining parser state
        for event in self._parser.flush():
            if isinstance(event, CommandEvent):
                text, result_event = await self._execute_command_inline(event)
                assistant_content += text
                yield result_event
            else:
                yield event

        self._last_assistant_content = assistant_content

    async def _execute_command_inline(
        self, event: CommandEvent
    ) -> tuple[str, CommandResultEvent]:
        """
        Execute a command event inline during text completion.

        Returns:
            Tuple of (text to append to assistant_content,
            CommandResultEvent to yield to caller).
        """
        result = await self._handle_command(event)
        if result.content:
            return (
                f"\n{result.content}\n",
                CommandResultEvent(command=event.command, content=result.content),
            )
        elif result.error:
            return (
                f"\n[Command Error: {result.error}]\n",
                CommandResultEvent(command=event.command, error=result.error),
            )
        return ("", CommandResultEvent(command=event.command))

    def _log_token_usage(self) -> None:
        """Extract and log token usage from the last LLM completion."""
        usage = self.llm.last_usage if hasattr(self.llm, "last_usage") else {}
        if usage:
            self._last_usage = usage
            logger.info(
                "Token usage",
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )

    async def _materialize_inline_file(self, part: FilePart) -> str | None:
        """Materialize inline browser-uploaded content to a temp file for ReadTool."""
        suffix = Path(part.name or "upload").suffix
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            if part.data_base64 is not None:
                temp_file.write(base64.b64decode(part.data_base64))
            elif part.content is not None:
                temp_file.write(part.content.encode("utf-8"))
            else:
                return None
            temp_file.flush()
            return temp_file.name
        finally:
            temp_file.close()

    async def _resolve_file_part(self, part: FilePart) -> list[ContentPart]:
        """Resolve a custom file part using the internal read tool."""
        temp_path: str | None = None
        path = part.path
        if (part.is_inline or part.data_base64 is not None) and not path:
            temp_path = await self._materialize_inline_file(part)
            path = temp_path
        elif part.content is not None and not path:
            label = part.name or part.path or "uploaded file"
            return [TextPart(text=f"File: {label}\n{part.content}")]

        if not path:
            return [TextPart(text="[File reference missing path/content]")]

        tool = self.registry.get_tool("read") if self.registry else None
        if tool is None:
            tool = ReadTool()
        if self.executor:
            context = self.executor._build_tool_context()
        else:
            return [
                TextPart(
                    text=f"[Unable to resolve file without executor context: {path}]"
                )
            ]

        try:
            result = await tool.execute({"path": path}, context=context)
            if result.error:
                return [
                    TextPart(
                        text=f"[File read failed: {part.name or path}: {result.error}]"
                    )
                ]
            if isinstance(result.output, str):
                return [TextPart(text=result.output)]
            return result.output
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Failed to clean temp upload", path=temp_path)

    async def _resolve_message_files(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve custom file parts in messages before provider send."""
        resolved_messages: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                resolved_messages.append(msg)
                continue

            raw_parts = []
            file_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "file":
                    file_data = item.get("file", {})
                    file_parts.append(
                        FilePart(
                            path=file_data.get("path"),
                            name=file_data.get("name"),
                            content=file_data.get("content"),
                            mime=file_data.get("mime"),
                            data_base64=file_data.get("data_base64"),
                            encoding=file_data.get("encoding"),
                            is_inline=bool(file_data.get("is_inline", False)),
                        )
                    )
                else:
                    raw_parts.append(item)

            if not file_parts:
                resolved_messages.append(msg)
                continue

            resolved_parts: list[ContentPart] = []
            file_map: dict[str, list[ContentPart]] = {}
            for idx, fp in enumerate(file_parts):
                resolved = await self._resolve_file_part(fp)
                file_map[str(idx)] = resolved
                if fp.name:
                    file_map[fp.name] = resolved
                if fp.path:
                    file_map[fp.path] = resolved

            inserted = False
            for item in raw_parts:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    pos = 0
                    for match in _FILE_PLACEHOLDER_RE.finditer(text):
                        if match.start() > pos:
                            resolved_parts.append(
                                TextPart(text=text[pos : match.start()])
                            )
                        ref = match.group("ref")
                        replacement = file_map.get(ref)
                        if replacement:
                            resolved_parts.extend(replacement)
                            inserted = True
                        else:
                            resolved_parts.append(TextPart(text=match.group(0)))
                        pos = match.end()
                    if pos < len(text):
                        resolved_parts.append(TextPart(text=text[pos:]))
                elif isinstance(item, dict) and item.get("type") == "image_url":
                    img = item.get("image_url", {})
                    meta = item.get("meta") or {}
                    resolved_parts.append(
                        ImagePart(
                            url=img.get("url", ""),
                            detail=img.get("detail", "low"),
                            source_type=meta.get("source_type"),
                            source_name=meta.get("source_name"),
                        )
                    )

            if not inserted:
                for fp in file_parts:
                    resolved_parts.extend(
                        file_map.get(fp.name)
                        or file_map.get(fp.path or "")
                        or file_map.get(str(file_parts.index(fp)), [])
                    )

            resolved_msg = dict(msg)
            resolved_msg["content"] = [part.to_dict() for part in resolved_parts]
            resolved_messages.append(resolved_msg)
        return resolved_messages

    async def run_once(self) -> AsyncIterator[ParseEvent]:
        """
        Run one controller turn.

        Collects pending events, runs LLM, and yields parse events.

        Yields:
            ParseEvents as they are detected in the LLM output
        """
        events = await self._collect_events()
        if not events:
            return

        logger.debug("Processing events", count=len(events))

        user_content, combined_text = self._build_turn_context(events)

        # In native mode, empty tool_complete events just trigger next turn
        # (tool results already added as role="tool" messages)
        skip_empty = self._is_native_mode and not combined_text.strip()
        if not skip_empty:
            self.conversation.append("user", user_content)

        messages = self.conversation.to_messages()
        messages = await self._resolve_message_files(messages)

        # Drain any messages queued by plugins via
        # ``PluginContext.inject_message_before_llm``. These are
        # inserted after the system prompt so they appear as early
        # context — and they're visible to ``pre_llm_call`` hooks.
        if self._pending_injections:
            injected = self._pending_injections
            self._pending_injections = []
            insert_idx = 0
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    insert_idx = i + 1
                else:
                    break
            messages = (
                list(messages[:insert_idx])
                + list(injected)
                + list(messages[insert_idx:])
            )

        # Plugin pre-hook: transform messages before LLM call
        if self.plugins:
            messages = await self.plugins.run_pre_hooks(
                "pre_llm_call",
                messages,
                model=getattr(self.llm, "model", ""),
                tools=self._get_native_tool_schemas() if self._is_native_mode else None,
            )

        logger.info("Generating response...")

        if self._is_native_mode:
            tool_schemas = self._get_native_tool_schemas()
            async for event in self._run_native_completion(messages, tool_schemas):
                yield event
        else:
            async for event in self._run_text_completion(messages):
                yield event
            self._log_token_usage()
            structured = self._collect_structured_assistant_parts()
            for part in structured:
                if isinstance(part, ImagePart):
                    yield AssistantImageEvent(
                        url=part.url,
                        detail=part.detail,
                        source_type=part.source_type,
                        source_name=part.source_name,
                        revised_prompt=getattr(part, "revised_prompt", None),
                    )
            self.conversation.append(
                "assistant",
                _merge_text_and_parts(self._last_assistant_content, structured),
            )

        # Plugin post_llm_call chain-with-return (cluster B.3). Logic
        # lives in ``controller_plugins.py`` to keep this file under
        # the 1000-line hard cap.
        if self.plugins:
            await run_post_llm_call_chain(self, messages)

    def register_command(
        self, command_name: str, cmd: Command, override: bool = False
    ) -> None:
        """Register a ``##name##`` controller command (cluster C.1)."""
        register_controller_command(self, command_name, cmd, override=override)

    async def _handle_command(self, event: CommandEvent) -> CommandResult:
        """Handle a framework command."""
        command = self._commands.get(event.command)
        if command is None:
            logger.warning("Unknown command", command=event.command)
            return CommandResult(error=f"Unknown command: {event.command}")

        logger.info("Executing command: %s", event.command)
        result = await command.execute(event.args, self._context)
        logger.debug(
            "Command result", command=event.command, has_content=bool(result.content)
        )
        return result

    def register_job(self, status: JobStatus) -> None:
        """Register a job status (for external tracking)."""
        self.job_store.register(status)

    def get_job_status(self, job_id: str) -> JobStatus | None:
        """Get job status."""
        return self.job_store.get_status(job_id)

    def has_pending_events(self) -> bool:
        """Check if there are pending events."""
        return not self._event_queue.empty() or len(self._pending_events) > 0

    def flush(self) -> None:
        """
        Clear conversation history (keep system prompt only).

        Used in ephemeral mode after completing an interaction.
        """
        self.conversation.clear(keep_system=True)
        logger.debug("Controller flushed (ephemeral mode)")

    @property
    def is_ephemeral(self) -> bool:
        """Check if controller is in ephemeral mode."""
        return self.config.ephemeral

    async def run_loop(
        self,
        on_text: Any | None = None,
        on_tool: Any | None = None,
        on_subagent: Any | None = None,
    ) -> None:
        """
        Run continuous controller loop.

        Args:
            on_text: Callback for text events
            on_tool: Callback for tool call events
            on_subagent: Callback for sub-agent call events
        """
        while True:
            async for event in self.run_once():
                if isinstance(event, TextEvent) and on_text:
                    on_text(event.text)
                elif isinstance(event, ToolCallEvent) and on_tool:
                    await on_tool(event)
                elif isinstance(event, SubAgentCallEvent) and on_subagent:
                    await on_subagent(event)
