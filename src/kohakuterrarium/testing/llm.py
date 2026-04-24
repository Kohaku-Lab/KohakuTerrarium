"""Scripted LLM provider for deterministic testing."""

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse
from kohakuterrarium.llm.message import Message


@dataclass
class ScriptEntry:
    """One LLM response in a test script.

    Attributes:
        response: Full response text (may contain tool call syntax like [/bash]...[bash/])
        match: If set, only use this entry if the last user message contains this string.
               If it doesn't match, skip to next entry.
        delay_per_chunk: Seconds to wait between yielding chunks (simulate latency)
        chunk_size: Characters per yield (1 = character-by-character streaming)
    """

    response: str
    match: str | None = None
    delay_per_chunk: float = 0
    chunk_size: int = 10  # Reasonable default for testing


class ScriptedLLM:
    """
    Deterministic LLM that follows a script of predefined responses.

    Implements the LLMProvider protocol for use in tests.
    Tracks all received messages for test assertions.
    Supports streaming simulation with configurable chunk sizes and delays.

    Usage:
        llm = ScriptedLLM([
            ScriptEntry("Hello! I'll help you."),
            ScriptEntry("[/bash]echo hello[bash/]"),
            ScriptEntry("Done with the task."),
        ])

        # After running:
        assert llm.call_count == 3
        assert "hello" in llm.call_log[0]  # first messages received
    """

    def __init__(self, script: list[ScriptEntry] | list[str] | None = None):
        """
        Args:
            script: List of ScriptEntry or plain strings (auto-wrapped).
                   If None, defaults to a single "OK" response.
        """
        if script is None:
            script = ["OK"]

        self.script: list[ScriptEntry] = []
        for entry in script:
            if isinstance(entry, str):
                self.script.append(ScriptEntry(response=entry))
            else:
                self.script.append(entry)

        self.call_count: int = 0
        self.call_log: list[list[dict[str, Any]]] = []  # All messages received per call

    def _normalize_messages(
        self,
        messages: list[Message] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert messages to dict format for logging."""
        if not messages:
            return []
        if isinstance(messages[0], dict):
            return messages  # type: ignore
        return [msg.to_dict() for msg in messages]  # type: ignore

    def _find_entry(self, messages: list[dict[str, Any]]) -> ScriptEntry:
        """Find the matching script entry for this call."""
        # Get last user message for matching
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    last_user = content
                elif isinstance(content, list):
                    last_user = " ".join(
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                break

        # Try entries starting from call_count
        idx = self.call_count
        while idx < len(self.script):
            entry = self.script[idx]
            if entry.match is None or entry.match in last_user:
                return entry
            idx += 1

        # Fallback: repeat last entry
        return self.script[-1]

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        normalized = self._normalize_messages(messages)
        self.call_log.append(normalized)
        entry = self._find_entry(normalized)
        self.call_count += 1

        # Yield in chunks for realistic streaming
        response = entry.response
        chunk_size = entry.chunk_size

        for i in range(0, len(response), chunk_size):
            chunk = response[i : i + chunk_size]
            yield chunk
            if entry.delay_per_chunk > 0:
                await asyncio.sleep(entry.delay_per_chunk)

    async def close(self) -> None:
        """Match the production LLM provider lifecycle API."""
        return None

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming convenience method."""
        parts: list[str] = []
        async for chunk in self.chat(messages, stream=False, **kwargs):
            parts.append(chunk)
        content = "".join(parts)
        return ChatResponse(
            content=content,
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model="scripted-test",
        )

    # =========================================================================
    # Assertion Helpers
    # =========================================================================

    @property
    def last_messages(self) -> list[dict[str, Any]] | None:
        """Get the most recent messages sent to the LLM."""
        return self.call_log[-1] if self.call_log else None

    @property
    def last_user_message(self) -> str:
        """Extract text of the last user message sent."""
        if not self.call_log:
            return ""
        for msg in reversed(self.call_log[-1]):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                return content if isinstance(content, str) else str(content)
        return ""
