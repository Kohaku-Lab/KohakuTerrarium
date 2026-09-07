"""Translate output events into websocket frames on an asynchronous queue."""

import asyncio
import os
import time
from collections import deque
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent

# The session and creature pair isolates replay history for each attachment target.
_event_logs: dict[str, list] = {}

# Reconnect replays need recent context, not the entire session. An unbounded
# list keeps a second full copy of every streamed frame in RAM for the process
# lifetime; a deque(maxlen=...) ring bounds that copy and keeps append+evict
# O(1) (list slicing would left-shift the whole buffer per frame).
# Overridable for lab harnesses.
_EVENT_LOG_MAX = int(os.environ.get("KT_EVENT_LOG_MAX", "5000"))


def get_event_log(key: str) -> deque:
    """Return the persistent in-memory replay ring for an attachment key."""
    if key not in _event_logs:
        _event_logs[key] = deque(maxlen=_EVENT_LOG_MAX)
    return _event_logs[key]


def _parse_detail(detail: str) -> tuple[str, str]:
    """Split an optional ``[name]`` prefix from activity detail text."""
    try:
        if detail.startswith("["):
            end = detail.index("] ", 1)
            return detail[1:end], detail[end + 2 :]
    except ValueError:
        try:
            if detail.startswith("[") and detail.endswith("]"):
                return detail[1:-1], ""
        except ValueError:
            pass
    return "unknown", detail


def _stream_metadata(metadata: dict) -> dict:
    """Whitelist activity metadata for streaming; prefer the bounded preview."""
    out = {}
    for k in _STREAM_METADATA_KEYS:
        if k in metadata:
            out[k] = metadata[k]
    # Full tool output lives in the session event log; stream frames only
    # carry the bounded preview so WS payloads stay small.
    if "output_preview" in metadata and "output" in out:
        out["output"] = metadata["output_preview"]
    return out


class StreamOutput(OutputModule):
    """Queue source-tagged websocket frames as a secondary agent output.

    When supplied, the live agent provides turn and branch identifiers for every
    frame. Those identifiers keep regeneration and edit-rerun streams attached to
    their originating branch even if the viewer switches branches mid-turn.
    """

    def __init__(
        self,
        source: str,
        queue: asyncio.Queue,
        log: deque,
        agent: Any | None = None,
    ):
        self._src = source
        self._q = queue
        self._log = log
        self._n = 0
        self._agent = agent

    def _current_turn_branch(self) -> tuple[int | None, int | None]:
        """Return positive current turn and branch IDs when both are assigned."""
        agent = self._agent
        if agent is None:
            return None, None
        ti = getattr(agent, "_turn_index", None)
        bi = getattr(agent, "_branch_id", None)
        if isinstance(ti, int) and ti > 0 and isinstance(bi, int) and bi > 0:
            return ti, bi
        return None, None

    def _put(self, msg: dict) -> None:
        msg["source"] = self._src
        msg["ts"] = time.time()
        # Explicit frame metadata wins; otherwise snapshot the live branch so delayed
        # rendering cannot attach output to the branch currently being viewed.
        ti, bi = self._current_turn_branch()
        if ti is not None and "turn_index" not in msg:
            msg["turn_index"] = ti
        if bi is not None and "branch_id" not in msg:
            msg["branch_id"] = bi
        self._q.put_nowait(msg)
        self._log.append(msg)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def write(self, text: str) -> None:
        self._put({"type": "text", "content": text})

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            self._put({"type": "text", "content": chunk})

    async def on_processing_start(self) -> None:
        self._put({"type": "processing_start"})

    async def on_processing_end(self) -> None:
        frame = {"type": "processing_end"}
        # A turn that ended only to wait for deliverable background jobs is not
        # a completion for attention purposes; tell the client so it does not
        # raise a user-attention edge. Mirrors the agent-private reads in
        # ``_current_turn_branch``.
        if getattr(self._agent, "_turn_dispatched_bg", None):
            frame["awaiting_background"] = True
        self._put(frame)

    def on_activity(self, activity_type: str, detail: str) -> None:
        name, info = _parse_detail(detail)
        frame_id = f"{activity_type}_{self._n}"
        self._put(
            {
                "type": "activity",
                "activity_type": activity_type,
                "name": name,
                "detail": info,
                "id": frame_id,
            }
        )
        self._emit_typed_mirror(activity_type, name, info, frame_id, metadata=None)
        self._n += 1

    def on_assistant_image(
        self,
        url: str,
        *,
        detail: str = "auto",
        source_type: str | None = None,
        source_name: str | None = None,
        revised_prompt: str | None = None,
    ) -> None:
        msg: dict = {"type": "image", "url": url, "detail": detail}
        meta: dict = {}
        if source_type is not None:
            meta["source_type"] = source_type
        if source_name is not None:
            meta["source_name"] = source_name
        if revised_prompt is not None:
            meta["revised_prompt"] = revised_prompt
        if meta:
            msg["meta"] = meta
        self._put(msg)
        self._n += 1

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict
    ) -> None:
        name, info = _parse_detail(detail)
        if activity_type.startswith("subagent_"):
            name = metadata.get("subagent") or name
        frame_id = f"{activity_type}_{self._n}"
        msg: dict = {
            "type": "activity",
            "activity_type": activity_type,
            "name": name,
            "detail": info,
            "id": frame_id,
        }
        if metadata:
            msg.update(_stream_metadata(metadata))
        self._put(msg)
        self._emit_typed_mirror(activity_type, name, info, frame_id, metadata)
        self._n += 1

    def _emit_typed_mirror(
        self,
        activity_type: str,
        name: str,
        detail: str,
        frame_id: str,
        metadata: dict | None,
    ) -> None:
        """Mirror tool and sub-agent lifecycle activities with a raw type.

        The frontend requires the wrapped ``type="activity"`` contract, while
        programmatic consumers dispatch on raw ``tool_*`` and ``subagent_*`` types.
        Mirroring only lifecycle events preserves both contracts without duplicating
        high-volume text or processing frames.
        """
        if not (
            activity_type.startswith("tool_") or activity_type.startswith("subagent_")
        ):
            return
        mirror: dict = {
            "type": activity_type,
            "activity_type": activity_type,
            "name": name,
            "detail": detail,
            "id": frame_id,
        }
        if metadata:
            mirror.update(_stream_metadata(metadata))
        self._put(mirror)

    async def emit(self, event: OutputEvent) -> None:
        """Convert a native output event without changing websocket compatibility.

        Legacy activity keys, metadata filtering, and ID sequencing remain stable.
        Rich UI event kinds use their own top-level frame types so the client can
        dispatch them directly.
        """
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str) and content:
                    self._put({"type": "text", "content": content})
            case "processing_start":
                frame = {"type": "processing_start"}
                if event.payload.get("request_id") is not None:
                    frame["request_id"] = event.payload["request_id"]
                self._put(frame)
            case "processing_end":
                # Same annotated frame as the hook path; a direct emit must
                # not bypass the awaiting_background flag.
                await self.on_processing_end()
            case "user_input":
                # User input is echoed by the attachment loop to avoid duplicate frames.
                pass
            case "assistant_image":
                payload = event.payload
                self.on_assistant_image(
                    payload["url"],
                    detail=payload.get("detail", "auto"),
                    source_type=payload.get("source_type"),
                    source_name=payload.get("source_name"),
                    revised_prompt=payload.get("revised_prompt"),
                )
            case "resume_batch":
                pass
            case (
                "ask_text"
                | "confirm"
                | "selection"
                | "progress"
                | "notification"
                | "card"
            ):
                # Rich UI payloads remain nested and unfiltered for direct client dispatch.
                msg: dict = {
                    "type": event.type,
                    "event_id": event.id,
                    "interactive": bool(event.interactive),
                    "surface": event.surface,
                    "payload": dict(event.payload),
                }
                if event.update_target is not None:
                    msg["update_target"] = event.update_target
                if event.timeout_s is not None:
                    msg["timeout_s"] = event.timeout_s
                self._put(msg)
                self._n += 1
            case "ui_supersede":
                self._put(
                    {
                        "type": "ui_supersede",
                        "event_id": event.payload.get("event_id"),
                    }
                )
            case _:
                detail = event.content if isinstance(event.content, str) else ""
                metadata = event.payload or {}
                if metadata:
                    self.on_activity_with_metadata(event.type, detail, metadata)
                else:
                    self.on_activity(event.type, detail)

    def on_supersede(self, event_id: str) -> None:
        """Notify the client that an interactive event no longer accepts replies."""
        self._put({"type": "ui_supersede", "event_id": event_id})


_STREAM_METADATA_KEYS = (
    "args",
    "job_id",
    "tools_used",
    "result",
    "output",
    "turns",
    "duration",
    "task",
    "trigger_id",
    "event_type",
    "drive_id",
    "drive_kind",
    "delivery_reason",
    "delivery_id",
    "objective",
    "channel",
    "sender",
    "content",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "round",
    "summary",
    "messages_compacted",
    "session_id",
    "llm_name",
    "model",
    "agent_name",
    "max_context",
    "compact_threshold",
    "error_type",
    "error",
    "messages_cleared",
    "background",
    "subagent",
    "tool",
    "interrupted",
    # Output-wiring frames need endpoint and source-event context for visualization.
    "from",
    "to",
    "with_content",
    "content_preview",
    "source_event_type",
    "source_turn_index",
    "turn_index",
    "branch_id",
    "pending_id",
    "final_state",
    # File-mutating tools may include a preview for immediate canvas rendering.
    "canvas_preview",
    # Bounded, tool-selected metadata such as generated artifact references.
    "tool_metadata",
    # Ordered assistant reasoning segments from the final LLM turn.
    "reasoning_content",
    "reasoning_summary",
    "reasoning_details",
    "reasoning",
    "_kt_assistant_segments",
)
