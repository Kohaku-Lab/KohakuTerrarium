"""Scripted LLM provider for deterministic testing."""

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse
from kohakuterrarium.llm.message import Message


@dataclass
class ScriptEntry:
    """Configure one response, optional user-text match, and stream timing."""

    response: str
    match: str | None = None
    delay_per_chunk: float = 0
    chunk_size: int = 10


class ScriptedLLM:
    """Return deterministic scripted responses and retain every call for assertions."""

    def __init__(self, script: list[ScriptEntry] | list[str] | None = None):
        """Initialize from entries or strings, defaulting to one ``"OK"`` response."""
        if script is None:
            script = ["OK"]

        self.script: list[ScriptEntry] = []
        for entry in script:
            if isinstance(entry, str):
                self.script.append(ScriptEntry(response=entry))
            else:
                self.script.append(entry)

        self.call_count: int = 0
        self.call_log: list[list[dict[str, Any]]] = []
        # Match-gated retries must advance through duplicate match entries.
        self._used_indices: set[int] = set()

    def _normalize_messages(
        self,
        messages: list[Message] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize message objects into dictionaries for call logs."""
        if not messages:
            return []
        if isinstance(messages[0], dict):
            return messages  # type: ignore
        return [msg.to_dict() for msg in messages]  # type: ignore

    def _find_entry(self, messages: list[dict[str, Any]]) -> ScriptEntry:
        """Select the next unused matching entry or sequential fallback."""
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

        # Match-gated entries take priority and are consumed once.
        for idx, entry in enumerate(self.script):
            if (
                entry.match is not None
                and entry.match in last_user
                and idx not in self._used_indices
            ):
                self._used_indices.add(idx)
                return entry

        idx = self.call_count
        while idx < len(self.script):
            entry = self.script[idx]
            if entry.match is None:
                self._used_indices.add(idx)
                return entry
            idx += 1

        # Exhausted scripts repeat the last entry for deterministic behavior.
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

        response = entry.response
        chunk_size = entry.chunk_size

        for i in range(0, len(response), chunk_size):
            chunk = response[i : i + chunk_size]
            yield chunk
            if entry.delay_per_chunk > 0:
                await asyncio.sleep(entry.delay_per_chunk)

    async def close(self) -> None:
        """Satisfy the production provider lifecycle contract."""
        return None

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        """Collect the scripted stream into a complete chat response."""
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

    @property
    def last_messages(self) -> list[dict[str, Any]] | None:
        """Return the most recent normalized request messages."""
        return self.call_log[-1] if self.call_log else None

    @property
    def last_user_message(self) -> str:
        """Return the latest logged user-message content as text."""
        if not self.call_log:
            return ""
        for msg in reversed(self.call_log[-1]):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                return content if isinstance(content, str) else str(content)
        return ""
