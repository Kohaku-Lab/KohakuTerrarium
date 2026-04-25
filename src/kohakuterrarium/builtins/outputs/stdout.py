"""
Standard output module.

Outputs to terminal/stdout with streaming support.
"""

import sys
from typing import TextIO

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.session.history import select_live_event_ids
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _write_safe(stream: TextIO, text: str) -> None:
    """Write text without letting console encoding mismatches crash streaming."""
    try:
        stream.write(text)
        return
    except UnicodeEncodeError:
        pass

    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(encoding, errors="replace").decode(encoding))


def _group_resume_events(events: list[dict]) -> list[dict]:
    """Group persisted events into condensed turns for the plain-stdout
    resume preview. Honors Wave C ``text_chunk`` events and shows only
    the latest branch of each turn (regen / edit+rerun siblings hidden
    by default; the navigator surfaces them).
    """
    if not events:
        return []
    live_ids = select_live_event_ids(events)
    turns: list[dict] = []
    current: dict = {"user": "", "text": "", "tools": []}

    for evt in events:
        etype = evt.get("type", "")
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid not in live_ids:
            continue
        if etype == "user_input":
            if current["user"] or current["text"]:
                turns.append(current)
            current = {
                "user": evt.get("content", ""),
                "text": "",
                "tools": [],
            }
        elif etype == "trigger_fired":
            if current["user"] or current["text"]:
                turns.append(current)
            channel = evt.get("channel", "")
            sender = evt.get("sender", "")
            current = {
                "user": f"[trigger: {channel} from {sender}]",
                "text": "",
                "tools": [],
            }
        elif etype in ("text", "text_chunk"):
            # text_chunk is Wave C's per-chunk streaming format; both
            # render the same way in the resume preview.
            current["text"] += evt.get("content", "")
        elif etype == "tool_call":
            name = evt.get("name", "tool")
            if name not in current["tools"]:
                current["tools"].append(name)

    if current["user"] or current["text"]:
        turns.append(current)
    return turns


class StdoutOutput(BaseOutputModule):
    """
    Standard output module.

    Writes content to stdout with streaming support.
    """

    def __init__(
        self,
        *,
        prefix: str = "",
        suffix: str = "\n",
        stream_suffix: str = "",
        flush_on_stream: bool = True,
    ):
        """
        Initialize stdout output.

        Args:
            prefix: Prefix to add before output (e.g., "Assistant: ")
            suffix: Suffix to add after complete output (e.g., newline)
            stream_suffix: Suffix for streaming chunks (usually empty)
            flush_on_stream: Whether to flush after each stream chunk
        """
        super().__init__()
        self.prefix = prefix
        self.suffix = suffix
        self.stream_suffix = stream_suffix
        self.flush_on_stream = flush_on_stream
        self._streaming = False
        self._has_output = False

    async def _on_start(self) -> None:
        """Initialize stdout output."""
        logger.debug("Stdout output started")

    async def _on_stop(self) -> None:
        """Cleanup stdout output."""
        logger.debug("Stdout output stopped")

    async def write(self, content: str) -> None:
        """
        Write complete content to stdout.

        Args:
            content: Content to write
        """
        if not content:
            return

        # Add prefix if this is start of output
        output = ""
        if not self._has_output and self.prefix:
            output += self.prefix

        output += content + self.suffix

        _write_safe(sys.stdout, output)
        sys.stdout.flush()

        self._has_output = True
        self._streaming = False

    async def write_stream(self, chunk: str) -> None:
        """
        Write a streaming chunk to stdout.

        Args:
            chunk: Chunk to write
        """
        if not chunk:
            return

        # Add prefix if this is start of output
        if not self._streaming and not self._has_output and self.prefix:
            _write_safe(sys.stdout, self.prefix)

        _write_safe(sys.stdout, chunk + self.stream_suffix)

        if self.flush_on_stream:
            sys.stdout.flush()

        self._streaming = True
        self._has_output = True

    async def flush(self) -> None:
        """Flush stdout and add suffix if streaming."""
        if self._streaming:
            _write_safe(sys.stdout, self.suffix)
        sys.stdout.flush()
        self._streaming = False

    def reset(self) -> None:
        """Reset output state for new conversation turn."""
        self._has_output = False
        self._streaming = False

    async def on_resume(self, events: list[dict]) -> None:
        """Show condensed session history on resume."""
        if not events:
            return

        turns = _group_resume_events(events)
        if not turns:
            return

        _write_safe(sys.stdout, f"\n--- Resumed session ({len(turns)} turns) ---\n")
        for turn in turns:
            if turn["user"]:
                user_preview = turn["user"][:120]
                if len(turn["user"]) > 120:
                    user_preview += "..."
                _write_safe(sys.stdout, f"You: {user_preview}\n")
            if turn["text"]:
                text_preview = turn["text"].strip()[:200]
                if len(turn["text"].strip()) > 200:
                    text_preview += "..."
                tools_str = ""
                if turn["tools"]:
                    tools_str = f" [used {', '.join(turn['tools'])}]"
                _write_safe(sys.stdout, f"Assistant:{tools_str} {text_preview}\n")
        _write_safe(sys.stdout, "--- End of history ---\n\n")
        sys.stdout.flush()


class PrefixedStdoutOutput(StdoutOutput):
    """
    Stdout output with configurable prefix per message.

    Useful for distinguishing different speakers in conversation.
    """

    def __init__(
        self,
        prefix: str = "Assistant: ",
        **kwargs,
    ):
        super().__init__(prefix=prefix, **kwargs)

    async def write_with_prefix(self, content: str, prefix: str | None = None) -> None:
        """
        Write content with optional custom prefix.

        Args:
            content: Content to write
            prefix: Custom prefix (uses default if None)
        """
        old_prefix = self.prefix
        if prefix is not None:
            self.prefix = prefix

        await self.write(content)

        self.prefix = old_prefix
