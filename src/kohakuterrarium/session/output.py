"""
SessionOutput - OutputModule that persists events to SessionStore.

Added as a secondary output on the agent's output router (same pattern
as the WS StreamOutput). Captures text, tool activity, processing state,
trigger events, and token usage without modifying the processing loop.
"""

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionOutput(OutputModule):
    """Output module that records events to a SessionStore.

    Accumulates streaming text chunks and flushes as one event
    on processing_end. Tool/subagent activity recorded immediately.
    Saves conversation snapshot and agent state after each processing cycle.
    """

    def __init__(self, agent_name: str, store: Any, agent: Any):
        self._agent_name = agent_name
        self._store = store
        self._agent = agent  # direct reference, not dict lookup
        self._text_buffer: list[str] = []
        # Cumulative API token usage across the session
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cached_tokens: int = 0

    def _record(self, event_type: str, data: dict) -> None:
        """Record an event."""
        try:
            self._store.append_event(self._agent_name, event_type, data)
        except Exception as e:
            logger.debug("Session record failed", error=str(e))

    async def start(self) -> None:
        # Restore cumulative token totals from session state
        try:
            usage = self._store.state.get(f"{self._agent_name}:token_usage")
            if isinstance(usage, dict):
                self._total_input_tokens = usage.get("total_input_tokens", 0)
                self._total_output_tokens = usage.get("total_output_tokens", 0)
                self._total_cached_tokens = usage.get("total_cached_tokens", 0)
        except (KeyError, TypeError):
            pass

    async def stop(self) -> None:
        pass

    async def write(self, text: str) -> None:
        if text:
            self._text_buffer.append(text)

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            self._text_buffer.append(chunk)

    async def flush(self) -> None:
        pass

    async def on_processing_start(self) -> None:
        self._text_buffer.clear()
        self._record("processing_start", {})

    async def on_processing_end(self) -> None:
        # Flush accumulated text as one event
        if self._text_buffer:
            text = "".join(self._text_buffer)
            if text.strip():
                self._record("text", {"content": text})
            self._text_buffer.clear()

        self._record("processing_end", {})

        # Save conversation snapshot (raw messages list, not JSON)
        try:
            if self._agent and hasattr(self._agent, "controller"):
                messages = self._agent.controller.conversation.to_messages()
                self._store.save_conversation(self._agent_name, messages)
        except Exception as e:
            logger.warning("Conversation snapshot failed", error=str(e))

        # Save agent state (scratchpad, turn count, token usage)
        try:
            if self._agent:
                state_kwargs = {}

                # Scratchpad
                if hasattr(self._agent, "session") and self._agent.session:
                    pad = self._agent.session.scratchpad
                    if hasattr(pad, "to_dict"):
                        state_kwargs["scratchpad"] = pad.to_dict()

                # Token usage from controller
                if hasattr(self._agent, "controller"):
                    usage = getattr(self._agent.controller, "_last_usage", {})
                    if usage:
                        state_kwargs["token_usage"] = usage

                if state_kwargs:
                    self._store.save_state(self._agent_name, **state_kwargs)
        except Exception as e:
            logger.debug("State save failed", error=str(e))

    def on_activity(self, activity_type: str, detail: str) -> None:
        name, info = _parse_detail(detail)
        self._flush_text_before_activity()
        self._record_activity(activity_type, name, info, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict
    ) -> None:
        name, info = _parse_detail(detail)
        self._flush_text_before_activity()
        self._record_activity(activity_type, name, info, metadata)

    def _flush_text_before_activity(self) -> None:
        if self._text_buffer:
            text = "".join(self._text_buffer)
            if text.strip():
                self._record("text", {"content": text})
            self._text_buffer.clear()

    # Dispatch table: activity_type -> handler method name
    _ACTIVITY_HANDLERS: dict[str, str] = {
        "trigger_fired": "_handle_trigger_fired",
        "tool_start": "_handle_tool_start",
        "tool_done": "_handle_tool_done",
        "tool_error": "_handle_tool_error",
        "subagent_start": "_handle_subagent_start",
        "subagent_done": "_handle_subagent_done",
        "subagent_error": "_handle_subagent_error",
        "token_usage": "_handle_token_usage",
        "compact_start": "_handle_compact_start",
        "compact_complete": "_handle_compact_complete",
        "processing_complete": "_handle_processing_complete",
        "processing_error": "_handle_processing_error",
        "context_cleared": "_handle_context_cleared",
    }

    def _record_activity(
        self, activity_type: str, name: str, detail: str, metadata: dict
    ) -> None:
        handler_name = self._ACTIVITY_HANDLERS.get(activity_type)
        if handler_name:
            getattr(self, handler_name)(name, detail, metadata)
        elif activity_type.startswith("subagent_tool_"):
            self._handle_subagent_tool(activity_type, name, detail, metadata)
        else:
            self._record(
                f"activity:{activity_type}",
                {"name": name, "detail": detail, **metadata},
            )

    def _handle_trigger_fired(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "trigger_fired",
            {
                "trigger_id": metadata.get("trigger_id", ""),
                "channel": metadata.get("channel", ""),
                "sender": metadata.get("sender", ""),
                "content": metadata.get("content", ""),
            },
        )

    def _handle_tool_start(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_call",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "args": metadata.get("args", {}),
            },
        )

    def _handle_tool_done(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_result",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "output": metadata.get("result", metadata.get("output", detail)),
                "exit_code": 0,
            },
        )

    def _handle_tool_error(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_result",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "output": metadata.get("result", detail),
                "exit_code": 1,
                "error": metadata.get("error", detail),
                "interrupted": bool(metadata.get("interrupted", False)),
                "cancelled": bool(metadata.get("cancelled", False)),
                "final_state": metadata.get("final_state", "error"),
            },
        )

    def _handle_subagent_start(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "subagent_call",
            {
                "name": name,
                "task": metadata.get("task", detail),
                "job_id": metadata.get("job_id", ""),
                "background": bool(metadata.get("background", False)),
            },
        )

    def _handle_subagent_done(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "subagent_result",
            {
                "name": name,
                "job_id": metadata.get("job_id", ""),
                "output": metadata.get("result", detail),
                "tools_used": metadata.get("tools_used", []),
                "turns": metadata.get("turns", 0),
                "duration": metadata.get("duration", 0),
                "total_tokens": metadata.get("total_tokens", 0),
                "prompt_tokens": metadata.get("prompt_tokens", 0),
                "completion_tokens": metadata.get("completion_tokens", 0),
            },
        )

    def _handle_subagent_error(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "subagent_result",
            {
                "name": name,
                "job_id": metadata.get("job_id", ""),
                "output": metadata.get("result", detail),
                "error": metadata.get("error", detail),
                "success": False,
                "interrupted": bool(metadata.get("interrupted", False)),
                "cancelled": bool(metadata.get("cancelled", False)),
                "final_state": metadata.get("final_state", "error"),
                "tools_used": metadata.get("tools_used", []),
                "turns": metadata.get("turns", 0),
                "duration": metadata.get("duration", 0),
                "total_tokens": metadata.get("total_tokens", 0),
                "prompt_tokens": metadata.get("prompt_tokens", 0),
                "completion_tokens": metadata.get("completion_tokens", 0),
            },
        )

    def _handle_token_usage(self, name: str, detail: str, metadata: dict) -> None:
        prompt = metadata.get("prompt_tokens", 0)
        completion = metadata.get("completion_tokens", 0)
        cached = metadata.get("cached_tokens", 0)
        self._total_input_tokens += prompt
        self._total_output_tokens += completion
        self._total_cached_tokens += cached
        self._record(
            "token_usage",
            {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": metadata.get("total_tokens", 0),
                "cached_tokens": cached,
            },
        )
        # Save cumulative totals to session state for fast resume
        try:
            self._store.save_state(
                self._agent_name,
                token_usage={
                    "total_input_tokens": self._total_input_tokens,
                    "total_output_tokens": self._total_output_tokens,
                    "total_cached_tokens": self._total_cached_tokens,
                    "last_prompt_tokens": prompt,
                },
            )
        except Exception as e:
            logger.debug(
                "Failed to save token usage state", error=str(e), exc_info=True
            )

    def _handle_compact_start(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_start",
            {"round": metadata.get("round", 0)},
        )

    def _handle_compact_complete(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_complete",
            {
                "round": metadata.get("round", 0),
                "summary": metadata.get("summary", ""),
                "messages_compacted": metadata.get("messages_compacted", 0),
            },
        )

    def _handle_subagent_tool(
        self, activity_type: str, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "subagent_tool",
            {
                "subagent": metadata.get("subagent", name),
                "tool_name": metadata.get("tool", ""),
                "activity": activity_type.replace("subagent_", ""),
                "detail": metadata.get("detail", detail),
                "job_id": metadata.get("job_id", ""),
            },
        )

    def _handle_context_cleared(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "context_cleared",
            {"messages_cleared": metadata.get("messages_cleared", 0)},
        )

    def _handle_processing_error(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "processing_error",
            {
                "error_type": metadata.get("error_type", "Error"),
                "error": metadata.get("error", detail),
            },
        )

    def _handle_processing_complete(
        self, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "processing_complete",
            {
                "trigger_channel": metadata.get("trigger_channel", ""),
                "trigger_sender": metadata.get("trigger_sender", ""),
                "output_preview": metadata.get("output_preview", ""),
            },
        )


def _parse_detail(detail: str) -> tuple[str, str]:
    """Extract [name] prefix from detail string.

    Handles nested brackets by finding ``] `` (closing bracket + space).
    """
    try:
        if detail.startswith("["):
            # Find "] " to handle labels with nested brackets like [name[id]]
            end = detail.index("] ", 1)
            return detail[1:end], detail[end + 2 :]
    except ValueError:
        # Fall back: no trailing content (bare [name])
        try:
            if detail.startswith("[") and detail.endswith("]"):
                return detail[1:-1], ""
        except ValueError:
            pass
    return "unknown", detail
