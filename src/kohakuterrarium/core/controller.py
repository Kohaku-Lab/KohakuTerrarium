"""LLM conversation orchestration for queued, multimodal agent events."""

import asyncio
import base64
import hashlib
import re
import tempfile
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

if TYPE_CHECKING:
    from kohakuterrarium.llm.base import ToolSchema

from kohakuterrarium.commands.base import Command, CommandResult
from kohakuterrarium.commands.read import (
    InfoCommand,
    JobsCommand,
    ReadCommand,
    WaitCommand,
)
from kohakuterrarium.core.controller_metrics import time_llm_call
from kohakuterrarium.core.controller_recovery import chat_with_recovery
from kohakuterrarium.core.controller_plugins import (
    register_controller_command,
    run_post_llm_call_chain,
)
from kohakuterrarium.core.controller_messages import (
    begin_assistant,
    end_assistant,
    _merge_text_and_parts,
)
from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.core.controller_context import (
    format_events_for_context,
    message_origin_metadata,
    tool_feedback_metadata,
)
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.inline_upload import materialize_upload
from kohakuterrarium.core.job import JobResult, JobStatus, JobStore
from kohakuterrarium.core.pinned import PinnedContext
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.tool_gate import visible_catalog
from kohakuterrarium.core.tool_output import materialize_image_part
from kohakuterrarium.errors import InvalidRequestError
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.message import (
    ContentPart,
    FilePart,
    ImagePart,
    TextPart,
    content_part_from_dict,
)
from kohakuterrarium.llm.tools import build_provider_native_tools, build_tool_schemas
from kohakuterrarium.modules.tool.doc_mode import DEFAULT_DOC_MODE
from kohakuterrarium.modules.plugin.base import ToolVisibility
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


def _file_part_from_item(item: Any) -> FilePart | None:
    if isinstance(item, FilePart):
        return item
    if isinstance(item, dict) and item.get("type") == "file":
        return content_part_from_dict(item)
    return None


def _file_ref_index(
    ref: str, n: int, names: dict[str, list[int]], paths: dict[str, list[int]]
) -> int | None:
    if ref.isdecimal():
        digits = "".join(str(int(digit)) for digit in ref).lstrip("0") or "0"
        if len(digits) > len(str(n)):
            return None
        idx = int(digits)
        return idx if idx < n else None
    hits = list(dict.fromkeys([*names.get(ref, ()), *paths.get(ref, ())]))
    if len(hits) > 1:
        raise InvalidRequestError(f"Ambiguous file reference: {ref}")
    return hits[0] if hits else None


def _schema_allowed(
    name: str, subagent_names: set[str], visibility: ToolVisibility
) -> bool:
    """Check one schema name against the applicable visibility category."""
    allowed = (
        visibility.allowed_subagents
        if name in subagent_names
        else visibility.allowed_tools
    )
    return allowed is None or name in allowed


@dataclass
class ControllerConfig:
    """Configure controller context, batching, retention, and tool parsing."""

    system_prompt: str = "You are a helpful assistant."
    include_job_status: bool = True
    include_tools_list: bool = True
    # Elide stale tool results only when the prompt is near the compact
    # threshold; with ample context the controller keeps full results so it
    # can reference what it read without re-running tools.
    elide_tool_results: bool = True
    batch_stackable_events: bool = True
    max_messages: int = 50
    ephemeral: bool = False
    known_outputs: set[str] = field(default_factory=set)
    tool_format: str | None = None
    # Compaction can separate native calls from results; providers must not see
    # either half of such an orphaned exchange.
    sanitize_orphan_tool_calls: bool = True
    tool_doc_mode: str = DEFAULT_DOC_MODE


@dataclass
class ControllerContext:
    """Expose controller runtime state to commands and handlers.

    The bootstrap layer supplies ``skills_registry`` and ``agent_path`` after
    construction because command documentation depends on the fully built agent.
    """

    controller: "Controller"
    job_store: JobStore
    registry: Registry
    skills_registry: Any | None = None
    agent_path: Any | None = None

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
    """Run queued events through an LLM and dispatch parsed actions."""

    def __init__(
        self,
        llm: LLMProvider,
        config: ControllerConfig | None = None,
        executor: Executor | None = None,
        registry: Registry | None = None,
    ):
        """Initialize the controller with optional shared execution state."""
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
        self.pinned = PinnedContext()

        # Token usage tracking
        self._last_usage: dict[str, int] = {}

        # The parent agent attaches this after construction so generated binary
        # content shares the graph's session lifecycle.
        self.session_store: Any = None

        # Controller-owned upload copies and every path authorized for this session.
        self._inline_temp_dir: Any = None
        self._inline_file_cache: dict[str, str] = {}
        self._inline_file_paths: set[str] = set()

        # Event queue
        self._event_queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        self._pending_events: list[TriggerEvent] = []

        # Parser state is rebuilt lazily because tools and sub-agents may be
        # registered after controller construction.
        self._parser_config: ParserConfig | None = None
        self._parser: StreamParser | None = None

        # Interrupt flag: checked during LLM streaming
        self._interrupted = False

        # The agent injects optional collaborators after all modules are built.
        self.plugins: Any = None

        # Post-LLM rewrites use the same router as streamed output so attached
        # consumers observe a coherent assistant turn.
        self.output_router: Any = None
        # Called before each LLM request so deferred prompt refreshes land between calls.
        self.before_llm_request: Callable[[], Any] | None = None
        # Capture the Message returned by append, not a later conversation tail.
        self._completed_assistant_message = None

        # Injections remain pending until the next round so every pre-LLM hook
        # receives the same augmented message list.
        self._pending_injections: list[dict] = []
        self._message_origin: dict[str, Any] | None = None
        # Path guidance belongs to the whole user turn, including tool rounds.
        self._skill_path_hint: str | None = None

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
        """Build a parser from the registry's current callable surface."""
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

    def _get_tool_visibility(self) -> ToolVisibility | None:
        """Collect plugin restrictions and renderer availability for this request."""
        return visible_catalog(
            self.registry,
            getattr(self, "plugins", None),
            getattr(self, "output_router", None),
        )

    def _get_native_tool_schemas(self) -> "list[ToolSchema]":
        """Build callable schemas, applying the visible tool catalog."""
        schemas = build_tool_schemas(
            self.registry, tool_doc_mode=self.config.tool_doc_mode
        )
        visibility = self._get_tool_visibility()
        if visibility is None:
            return schemas
        subagent_names = set(self.registry.list_subagents())
        return [
            schema
            for schema in schemas
            if _schema_allowed(schema.name, subagent_names, visibility)
        ]

    def _get_provider_native_tools(self) -> list:
        """Collect tools the active provider should translate into
        wire-format built-in tool specs. See
        :func:`build_provider_native_tools`."""
        tools = build_provider_native_tools(self.registry)
        visibility = self._get_tool_visibility()
        if visibility is None or visibility.allowed_tools is None:
            return tools
        return [tool for tool in tools if tool.tool_name in visibility.allowed_tools]

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
        return format_events_for_context(events)

    def _build_turn_context(
        self, events: list[TriggerEvent]
    ) -> tuple[str | list[ContentPart], str]:
        """Build user content; combined_text is only for empty-input detection."""
        job = self.job_store.format_context() if self.config.include_job_status else ""
        content = self._format_events_for_context(events)
        if isinstance(content, str):
            combined = "\n\n".join([job, content] if job else [content])
            return combined, combined
        parts: list[ContentPart] = ([TextPart(text=job)] if job else []) + list(content)
        combined = "\n\n".join(p.text for p in parts if isinstance(p, TextPart))
        return parts, combined

    def _chat_with_recovery(
        self, messages: list[dict], **kwargs: Any
    ) -> AsyncIterator[str]:
        """Bind this controller's output router to its model request."""
        kwargs["media_session_path"] = getattr(self.session_store, "path", None)
        return chat_with_recovery(self.llm, self.output_router, messages, **kwargs)

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

        with time_llm_call(self.llm) as _t:
            async with aclosing(
                self._chat_with_recovery(
                    messages,
                    stream=True,
                    tools=tool_schemas or None,
                    provider_native_tools=provider_native_tools or None,
                )
            ) as stream:
                async for chunk in stream:
                    if self._interrupted:
                        _t.status = "interrupted"
                        break
                    assistant_content += chunk
                    if chunk:
                        yield TextEvent(text=chunk)
        self._log_token_usage()

        # Materialize binary output before recording it so the conversation
        # remains small and later readers use stable session URLs.
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

        # Some providers require opaque reasoning state to round-trip on the
        # next request, including completions that contain no tool call.
        extra_fields = getattr(self.llm, "last_assistant_extra_fields", {}) or {}
        final_content = _merge_text_and_parts(assistant_content, structured_parts)
        append_kwargs: dict = {"extra_fields": extra_fields}
        pending_native_events: list[ParseEvent] = []

        if native_calls:
            tool_calls_data = []
            known_subagents = set(self.registry.list_subagents())
            for tc in native_calls:
                tool_calls_data.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                )
                logger.info(
                    "Native tool call",
                    tool_name=tc.name,
                    tool_args=tc.arguments[:100],
                )
                call_args = {**tc.parsed_arguments(), "_tool_call_id": tc.id}
                if tc.name in known_subagents:
                    pending_native_events.append(
                        SubAgentCallEvent(
                            name=tc.name, args=call_args, raw=tc.arguments
                        )
                    )
                else:
                    pending_native_events.append(
                        ToolCallEvent(name=tc.name, args=call_args, raw=tc.arguments)
                    )
            append_kwargs["tool_calls"] = tool_calls_data

        # Record the provider's complete call announcement before dispatching any
        # execution event. Background handlers may append a tool placeholder as
        # soon as the event is yielded, and provider protocols require that result
        # to follow the assistant message which introduced its tool_call_id.
        self._completed_assistant_message = self.conversation.append(
            "assistant", final_content, **append_kwargs
        )

        for event in pending_native_events:
            yield event

    def _collect_structured_assistant_parts(self) -> list[ContentPart]:
        """Materialize structured content captured by the provider.

        Inline images move into session artifacts and receive stable served URLs;
        providers without structured output produce an empty list.
        """
        source = getattr(self.llm, "last_assistant_content_parts", None)
        if source is None:
            return []
        materialized: list[ContentPart] = []
        for part in source:
            if isinstance(part, ImagePart):
                rewritten = materialize_image_part(
                    part,
                    getattr(self, "session_store", None),
                    subdir="generated_images",
                    stem_hint=part.source_name,
                    elide_without_store=False,
                )
                materialized.append(
                    rewritten if isinstance(rewritten, ImagePart) else part
                )
            else:
                materialized.append(part)
        return materialized

    def _persist_image_part(self, part: ImagePart) -> ImagePart:
        """Persist a data-URL image and return a session-backed part.

        Without a session store, or for an unrecognized URL, the original part
        is retained so ephemeral agents do not lose generated content.
        """
        rewritten = materialize_image_part(
            part,
            getattr(self, "session_store", None),
            subdir="generated_images",
            stem_hint=part.source_name,
            elide_without_store=False,
        )
        return rewritten if isinstance(rewritten, ImagePart) else part

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

        with time_llm_call(self.llm) as _t:
            async with aclosing(
                self._chat_with_recovery(
                    messages,
                    stream=True,
                    provider_native_tools=provider_native_tools or None,
                )
            ) as stream:
                async for chunk in stream:
                    if self._interrupted:
                        _t.status = "interrupted"
                        break
                    assistant_content += chunk

                    for event in self._parser.feed(chunk):
                        if isinstance(event, CommandEvent):
                            text, result_event = await self._execute_command_inline(
                                event
                            )
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
        """Materialize and authorize one inline upload for the live session."""
        if part.data_base64 is not None:
            raw = base64.b64decode(part.data_base64)
        elif part.content is not None:
            raw = part.content.encode("utf-8")
        else:
            return None

        name = part.name or "attachment"
        cache_key = f"{hashlib.sha256(raw).hexdigest()}:{name}"
        if self._inline_temp_dir is None:
            self._inline_temp_dir = tempfile.TemporaryDirectory(
                prefix="kohakuterrarium-attachments-"
            )
        path = materialize_upload(
            raw,
            name,
            self._inline_temp_dir.name,
            self._inline_file_cache.get(cache_key),
        )
        self._inline_file_cache[cache_key] = path
        self._inline_file_paths.add(path)

        guard = getattr(self.executor, "_path_guard", None) if self.executor else None
        if guard is not None and hasattr(guard, "allow_session_path"):
            guard.allow_session_path(path)
        return path

    def cleanup_inline_files(self) -> None:
        """Revoke and remove every controller-owned live-session attachment."""
        guard = getattr(self.executor, "_path_guard", None) if self.executor else None
        if guard is not None and hasattr(guard, "revoke_session_path"):
            for path in self._inline_file_paths:
                guard.revoke_session_path(path)
        self._inline_file_cache.clear()
        self._inline_file_paths.clear()
        if self._inline_temp_dir is not None:
            self._inline_temp_dir.cleanup()
            self._inline_temp_dir = None

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
            from kohakuterrarium.builtins.tools.read import ReadTool

            tool = ReadTool()
        if self.executor:
            context = self.executor._build_tool_context()
        else:
            return [
                TextPart(
                    text=f"[Unable to resolve file without executor context: {path}]"
                )
            ]

        result = await tool.execute({"path": path}, context=context)
        if result.error:
            return [
                TextPart(
                    text=f"[File read failed: {part.name or path}: {result.error}]"
                )
            ]
        resolved = (
            [TextPart(text=result.output)]
            if isinstance(result.output, str)
            else result.output
        )
        if temp_path:
            label = part.name or Path(temp_path).name
            return [
                TextPart(text=f"Attached file: {label}\nPath: {temp_path}"),
                *resolved,
            ]
        return resolved

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

            file_parts: list[FilePart] = []
            file_idx_at: dict[int, int] = {}
            name_hits: dict[str, list[int]] = {}
            path_hits: dict[str, list[int]] = {}
            for i, item in enumerate(content):
                fp = _file_part_from_item(item)
                if fp is None:
                    continue
                idx = len(file_parts)
                file_parts.append(fp)
                file_idx_at[i] = idx
                if fp.name:
                    name_hits.setdefault(fp.name, []).append(idx)
                if fp.path:
                    path_hits.setdefault(fp.path, []).append(idx)
            if not file_parts:
                resolved_messages.append(msg)
                continue

            ref_index: dict[str, int | None] = {}
            referenced: set[int] = set()
            for item in content:
                if isinstance(item, TextPart):
                    text = item.text
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text") or ""
                else:
                    continue
                for match in _FILE_PLACEHOLDER_RE.finditer(text):
                    ref = match.group("ref")
                    if ref not in ref_index:
                        ref_index[ref] = _file_ref_index(
                            ref, len(file_parts), name_hits, path_hits
                        )
                    hit = ref_index[ref]
                    if hit is not None:
                        referenced.add(hit)

            resolved_by_idx: dict[int, list[ContentPart]] = {}
            for idx, fp in enumerate(file_parts):
                resolved_by_idx[idx] = await self._resolve_file_part(fp)

            resolved_parts: list[ContentPart | dict[str, Any]] = []
            for i, item in enumerate(content):
                if i in file_idx_at:
                    idx = file_idx_at[i]
                    if idx not in referenced:
                        resolved_parts.extend(resolved_by_idx[idx])
                    continue
                if isinstance(item, ImagePart):
                    resolved_parts.append(item)
                    continue
                if isinstance(item, dict) and item.get("type") == "image_url":
                    resolved_parts.append(deepcopy(item))
                    continue
                if isinstance(item, TextPart):
                    text = item.text
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text") or ""
                else:
                    continue
                original_part = item.to_dict() if isinstance(item, TextPart) else item
                if not _FILE_PLACEHOLDER_RE.search(text):
                    resolved_parts.append(deepcopy(original_part))
                    continue
                pos = 0
                for match in _FILE_PLACEHOLDER_RE.finditer(text):
                    if match.start() > pos:
                        resolved_parts.append(
                            {
                                **deepcopy(original_part),
                                "text": text[pos : match.start()],
                            }
                        )
                    hit = ref_index.get(match.group("ref"))
                    if hit is None:
                        resolved_parts.append(
                            {**deepcopy(original_part), "text": match.group(0)}
                        )
                    else:
                        resolved_parts.extend(resolved_by_idx[hit])
                    pos = match.end()
                if pos < len(text):
                    resolved_parts.append(
                        {**deepcopy(original_part), "text": text[pos:]}
                    )

            resolved_msg = dict(msg)
            resolved_msg["content"] = [
                part if isinstance(part, dict) else part.to_dict()
                for part in resolved_parts
            ]
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

        origin = message_origin_metadata(self)
        user_content, combined_text = self._build_turn_context(events)
        # Tool continuations and unedited regeneration reuse existing context;
        # appending an empty user message would corrupt provider turn ordering.
        skip_empty = (
            isinstance(user_content, str)
            and not combined_text.strip()
            and (self._is_native_mode or all(e.type == "tool_complete" for e in events))
        ) or any(
            e.type == "user_input"
            and e.context.get("rerun")
            and not e.context.get("edited")
            for e in events
        )
        if not skip_empty:
            msg = self.conversation.append("user", user_content)
            # Pure tool-feedback rounds are tagged so their results can
            # be elided once the round is no longer the latest; a batch
            # carrying any real user input is never tagged (and never
            # elided).
            if events and all(e.type == EventType.TOOL_COMPLETE for e in events):
                msg.metadata.update(tool_feedback_metadata(events))
            msg.metadata.update(origin)

        if self.before_llm_request is not None:
            self.before_llm_request()
        messages = self.pinned.apply(self.conversation.to_messages())
        messages = await self._resolve_message_files(messages)

        # Insert after the system block and pins so plugin context is early
        # while remaining visible to every pre-LLM hook.
        injected = self._pending_injections
        self._pending_injections = []
        if self._skill_path_hint:
            # A fresh dict prevents a pre-hook from mutating the turn snapshot.
            injected = [{"role": "user", "content": self._skill_path_hint}, *injected]
        if injected:
            insert_idx = self.pinned.context_end(messages)
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

        await begin_assistant(self)

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
            self._completed_assistant_message = self.conversation.append(
                "assistant",
                _merge_text_and_parts(self._last_assistant_content, structured),
                extra_fields=(
                    getattr(self.llm, "last_assistant_extra_fields", {}) or {}
                ),
            )

        # Post hooks run after conversation append because rewrites must update
        # the canonical assistant message rather than only the streamed copy.
        if self.plugins:
            await run_post_llm_call_chain(self, messages)
        await end_assistant(self)

    def register_command(
        self, command_name: str, cmd: Command, override: bool = False
    ) -> None:
        """Register a ``##name##`` controller command."""
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
        self._skill_path_hint = None
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
