"""Deferred LLM provider for "no model configured yet" state.

Keep creatures constructible before a usable model is selected.

The placeholder matches the provider protocol and defers the original setup
error until a chat request, allowing runtime model switching to recover.
"""

from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse, NativeToolCall
from kohakuterrarium.llm.message import Message


class DeferredLLMProvider:
    """Placeholder provider that postpones configuration errors until chat."""

    # Empty compatibility metadata keeps native-tool discovery safe.
    provider_name: str = ""
    provider_native_tools: frozenset[str] = frozenset()

    def __init__(self, reason: str = "no LLM model configured") -> None:
        self.reason = reason
        self._profile_max_context = 8192
        self._last_tool_calls: list[NativeToolCall] = []

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        return self._last_tool_calls

    def _raise(self) -> None:
        raise RuntimeError(
            f"This creature has no usable LLM provider yet: {self.reason}.  "
            "Pick a model via the Studio UI (model selector) or run "
            "``switch_model`` to bind one — the creature itself is live "
            "and will reuse its conversation history once a model is set."
        )

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        tools: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        self._raise()
        # Keep the async-generator contract even though the error always raises.
        yield ""  # pragma: no cover

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        self._raise()
        return ChatResponse(  # pragma: no cover - _raise always exits
            content="",
            finish_reason="error",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model="deferred",
        )

    async def close(self) -> None:
        return None


__all__ = ["DeferredLLMProvider"]
