"""Deferred LLM provider for "no model configured yet" state.

Model selection is a runtime concern — the user picks a model in the
Studio UI, swaps it via ``switch_model``, or uses ``/model`` from the
chat composer.  Creature **creation** must therefore succeed even when
no usable LLM is configured: a creature with a deferred provider runs,
holds its conversation, accepts inputs, lists in the runtime graph; the
"select a model" error surfaces only when a chat turn actually tries to
call the LLM.

Replacing the deferred provider with a working one is what
``switch_model`` does internally — the engine rebuilds ``self.llm`` via
:func:`bootstrap.llm.create_llm_from_profile_name`, the deferred
instance is discarded, and the next chat turn streams normally.

The provider returns the same Protocol shape as a real provider so
nothing else in the agent runtime needs to know about it.  The error
message threads the original construction failure so the operator can
fix the underlying issue (missing key, unknown profile, etc.).
"""

from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse, NativeToolCall
from kohakuterrarium.llm.message import Message


class DeferredLLMProvider:
    """A placeholder provider that raises only on chat()/chat_complete().

    Construction NEVER fails.  Used when the agent build's real
    provider construction raised (e.g. missing API key) AND the agent
    must still exist so the user can pick a model at runtime.

    ``reason`` is surfaced verbatim in the runtime-error message that
    a chat turn produces — usually the ``ValueError`` text from
    ``_create_from_profile``.
    """

    # Class-level defaults so the auto-tool-injection path in
    # ``bootstrap.agent_init`` sees a sane (empty) native-tool set
    # instead of an AttributeError.
    provider_name: str = ""
    provider_native_tools: frozenset[str] = frozenset()

    def __init__(self, reason: str = "no LLM model configured") -> None:
        self.reason = reason
        self._profile_max_context = 8192
        # Mirrors the public surface of the real providers — callers
        # read this property; on a deferred provider there are no
        # recorded tool calls.
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
        # Unreachable — ``_raise`` always throws.  ``yield`` keeps the
        # function as an async generator so ``async for`` doesn't trip
        # on a coroutine-returning callable.
        yield ""  # pragma: no cover

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        self._raise()
        return ChatResponse(  # pragma: no cover - unreachable
            content="",
            finish_reason="error",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model="deferred",
        )

    async def close(self) -> None:
        return None


__all__ = ["DeferredLLMProvider"]
