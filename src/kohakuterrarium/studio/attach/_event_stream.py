"""Stream output bridge — :class:`OutputModule` over an asyncio.Queue.

Was ``api/events.py:StreamOutput`` + ``get_event_log``.  Lives under
``studio/attach`` because it is the bridge the IO attach uses to
translate engine events to WS frames.  Other transports (CLI tail,
TTS) may grow their own variants — this one is the WS-shaped one.
"""

import asyncio
import time

from kohakuterrarium.modules.output.base import OutputModule

# In-memory event logs keyed by ``"{session_id}:{creature_id}"``.
_event_logs: dict[str, list] = {}


def get_event_log(key: str) -> list:
    """Get or create an event log for a mount key."""
    if key not in _event_logs:
        _event_logs[key] = []
    return _event_logs[key]


def _parse_detail(detail: str) -> tuple[str, str]:
    """Extract a ``[name]`` prefix from a detail string."""
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


class StreamOutput(OutputModule):
    """Secondary output that tags events with source and pushes to a
    shared queue.  Attached to creatures' agents as a secondary sink."""

    def __init__(self, source: str, queue: asyncio.Queue, log: list):
        self._src = source
        self._q = queue
        self._log = log
        self._n = 0

    def _put(self, msg: dict) -> None:
        msg["source"] = self._src
        msg["ts"] = time.time()
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
        self._put({"type": "processing_end"})

    def on_activity(self, activity_type: str, detail: str) -> None:
        name, info = _parse_detail(detail)
        self._put(
            {
                "type": "activity",
                "activity_type": activity_type,
                "name": name,
                "detail": info,
                "id": f"{activity_type}_{self._n}",
            }
        )
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
        msg: dict = {
            "type": "activity",
            "activity_type": activity_type,
            "name": name,
            "detail": info,
            "id": f"{activity_type}_{self._n}",
        }
        if metadata:
            for k in (
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
                "final_state",
            ):
                if k in metadata:
                    msg[k] = metadata[k]
        self._put(msg)
        self._n += 1
