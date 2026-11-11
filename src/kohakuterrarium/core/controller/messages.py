"""Capture completed assistant messages and emit round output events."""

import asyncio
from copy import deepcopy
from typing import Any

from kohakuterrarium.parsing import AssistantImageEvent, ParseEvent
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.llm.message import ContentPart, ImagePart, TextPart
from kohakuterrarium.modules.output.event import OutputEvent

logger = get_logger(__name__)


def assistant_image_events(parts: list[ContentPart]) -> list[AssistantImageEvent]:
    """Project received image parts into display events."""
    return [
        AssistantImageEvent(
            url=part.url,
            detail=part.detail,
            source_type=part.source_type,
            source_name=part.source_name,
            revised_prompt=getattr(part, "revised_prompt", None),
        )
        for part in parts
        if isinstance(part, ImagePart)
    ]


async def deliver_interrupted_output(controller: Any, events: list[ParseEvent]) -> None:
    """Deliver buffered display events without replacing the interrupted outcome."""
    if controller.output_router is None:
        return
    for event in events:
        try:
            await controller.output_router.route(event)
        except Exception as output_error:
            logger.warning(
                "Interrupted output delivery failed",
                error_type=type(output_error).__name__,
            )


def _merge_text_and_parts(
    text: str, structured_parts: list[ContentPart]
) -> str | list[ContentPart]:
    """Combine streamed text with structured parts (text-first list)."""
    if not structured_parts:
        return text
    merged: list[ContentPart] = []
    if text:
        merged.append(TextPart(text=text))
    merged.extend(structured_parts)
    return merged


async def begin_assistant(controller: Any) -> None:
    """Clear the captured assistant message and emit round start if routed."""
    controller._completed_assistant_message = None
    router = controller.output_router
    if router is not None:
        await router.emit(OutputEvent(type="assistant_round_start", payload={}))


async def record_incomplete_assistant(
    controller: Any, content: str | list[ContentPart], error: BaseException
) -> None:
    """Retain received content as an incomplete canonical assistant message."""
    if not content:
        return
    origin = next(
        (
            {
                key: msg.metadata[key]
                for key in ("turn_index", "branch_id", "origin_known")
                if key in msg.metadata
            }
            for msg in reversed(controller.conversation.get_messages())
            if "turn_index" in msg.metadata
        ),
        {},
    )
    metadata = {
        **origin,
        "incomplete": True,
        "completion_state": (
            "interrupted"
            if isinstance(error, (asyncio.CancelledError, GeneratorExit))
            else "incomplete"
        ),
    }
    outcome = getattr(error, "response_outcome", None)
    if isinstance(outcome, dict):
        metadata["response_outcome"] = dict(outcome)
    controller._completed_assistant_message = controller.conversation.append(
        "assistant",
        content,
        metadata=metadata,
    )
    try:
        await end_assistant(controller)
    except Exception as output_error:
        logger.warning(
            "Incomplete assistant delivery failed",
            error_type=type(output_error).__name__,
        )


async def end_assistant(controller: Any) -> None:
    """Emit a detached copy of the captured assistant message after post-hooks."""
    captured = controller._completed_assistant_message
    router = controller.output_router
    if captured is None or router is None:
        return
    message = deepcopy(captured.to_dict())
    if captured.metadata:
        message["metadata"] = deepcopy(captured.metadata)
    await router.emit(
        OutputEvent(type="assistant_message", payload={"message": message})
    )
