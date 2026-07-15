"""Provide a deterministic LLM that still exercises real profile resolution.

The provider re-reads ``{"script": [...]}`` from ``script_path`` each turn and
falls back to ``"OK"`` when absent or invalid. It retains the resolved API key
so tests can verify identity lookup without making network requests.
"""

import json
from pathlib import Path
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse, LLMProvider
from kohakuterrarium.llm.message import Message


class FakeLLMProvider(LLMProvider):
    """Return scripted responses while exposing the profile-resolved API key."""

    provider_name = "fake_test"
    provider_native_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "fake-echo",
        script_path: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.script_path = Path(script_path) if script_path else None
        self.call_count = 0

    def _load_script(self) -> list[str]:
        if self.script_path is None:
            return ["OK"]
        try:
            data = json.loads(self.script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ["OK"]
        script = data.get("script") if isinstance(data, dict) else None
        if not isinstance(script, list):
            return ["OK"]
        return [str(s) for s in script]

    def _pick(self) -> str:
        script = self._load_script()
        if not script:
            return "OK"
        idx = min(self.call_count, len(script) - 1)
        self.call_count += 1
        return script[idx]

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        text = self._pick()
        # Two chunks ensure callers exercise incremental response handling.
        mid = max(1, len(text) // 2)
        yield text[:mid]
        if text[mid:]:
            yield text[mid:]

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        text = self._pick()
        return ChatResponse(
            content=text,
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
        )

    async def close(self) -> None:
        return None


__all__ = ["FakeLLMProvider"]
