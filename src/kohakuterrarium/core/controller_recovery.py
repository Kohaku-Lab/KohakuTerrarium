"""Bind upstream recovery notifications to one main controller request."""

from pathlib import Path
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.media.reference import session_media_scope
from kohakuterrarium.llm.recovery.status import observe_model_recovery
from kohakuterrarium.llm.stream.tool_progress import observe_tool_call_progress
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter


async def chat_with_recovery(
    llm: LLMProvider,
    output_router: OutputRouter | None,
    messages: list[dict],
    *,
    media_session_path: str | Path | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Bind recovery observers and session media to provider execution steps."""

    async def notify(payload: dict) -> None:
        if output_router is not None:
            await output_router.emit(
                OutputEvent(type="model_recovery", surface="status", payload=payload)
            )

    async def progress(payload: dict) -> None:
        if output_router is not None:
            await output_router.emit(
                OutputEvent(
                    type="tool_args_progress", surface="status", payload=payload
                )
            )

    with session_media_scope(media_session_path):
        stream = llm.chat(messages, **kwargs)
    try:
        while True:
            with (
                observe_model_recovery(notify),
                observe_tool_call_progress(progress),
                session_media_scope(media_session_path),
            ):
                try:
                    chunk = await anext(stream)
                except StopAsyncIteration:
                    return
            yield chunk
    finally:
        with observe_model_recovery(notify), session_media_scope(media_session_path):
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
