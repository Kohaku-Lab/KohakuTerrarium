"""Bind upstream recovery notifications to one main controller request."""

from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.model_recovery_status import observe_model_recovery
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter


async def chat_with_recovery(
    llm: LLMProvider,
    output_router: OutputRouter | None,
    messages: list[dict],
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Observe only provider execution, never a consumer or inherited child task."""

    async def notify(payload: dict) -> None:
        if output_router is not None:
            await output_router.emit(
                OutputEvent(type="model_recovery", surface="status", payload=payload)
            )

    stream = llm.chat(messages, **kwargs)
    try:
        while True:
            with observe_model_recovery(notify):
                try:
                    chunk = await anext(stream)
                except StopAsyncIteration:
                    return
            yield chunk
    finally:
        with observe_model_recovery(notify):
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
