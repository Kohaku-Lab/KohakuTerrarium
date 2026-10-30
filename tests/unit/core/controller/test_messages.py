"""Unit tests for :mod:`kohakuterrarium.core.controller.messages`."""

import asyncio
from copy import deepcopy

import pytest

from kohakuterrarium.core.controller.controller import Controller, ControllerConfig
from kohakuterrarium.core.controller.messages import (
    begin_assistant,
    end_assistant,
)
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.output import OutputRecorder


def _assistant_message(
    conversation: Conversation,
    *,
    content=None,
    tool_calls=None,
    extra_fields=None,
):
    if content is None:
        content = [
            TextPart(text="hello"),
            ImagePart(url="https://example.invalid/a.png"),
        ]
    return conversation.append(
        "assistant",
        content,
        tool_calls=tool_calls
        or [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"msg": "hi"}'},
            }
        ],
        extra_fields=extra_fields or {"reasoning_content": "think"},
    )


def _controller(*, router=None, script=None, native=False) -> Controller:
    controller = Controller(
        ScriptedLLM(script or ["hello"]),
        ControllerConfig(
            tool_format="native" if native else None, include_job_status=False
        ),
    )
    controller.output_router = router
    return controller


def _event_types(recorder: OutputRecorder) -> list[str]:
    return [event.type for event in recorder.events]


def _message_payload(recorder: OutputRecorder) -> dict:
    messages = [event for event in recorder.events if event.type == "assistant_message"]
    assert messages, "expected assistant_message event"
    return messages[-1].payload["message"]


class TestBeginAssistant:
    async def test_emits_round_start_and_clears_capture(self):
        recorder = OutputRecorder()
        controller = _controller(router=recorder)
        controller._completed_assistant_message = _assistant_message(
            controller.conversation
        )

        await begin_assistant(controller)

        assert controller._completed_assistant_message is None
        assert _event_types(recorder) == ["assistant_round_start"]
        assert recorder.events[0].payload == {}

    async def test_no_router_clears_capture_without_emit(self):
        controller = _controller(router=None)
        controller._completed_assistant_message = object()

        await begin_assistant(controller)

        assert controller._completed_assistant_message is None


class TestEndAssistant:
    async def test_emits_detached_copy_with_parts_calls_and_extras(self):
        recorder = OutputRecorder()
        conversation = Conversation()
        captured = _assistant_message(conversation)
        controller = _controller(router=recorder)
        controller.conversation = conversation
        controller._completed_assistant_message = captured
        original = deepcopy(captured.to_dict())

        await end_assistant(controller)

        assert _event_types(recorder) == ["assistant_message"]
        payload = _message_payload(recorder)
        assert payload["content"][0]["text"] == "hello"
        assert payload["content"][1]["type"] == "image_url"
        assert payload["content"][1]["image_url"]["url"] == (
            "https://example.invalid/a.png"
        )
        assert payload["tool_calls"][0]["id"] == "c1"
        assert payload["tool_calls"][0]["function"]["name"] == "echo"
        assert payload["reasoning_content"] == "think"
        assert "[image]" not in str(payload["content"])
        assert payload["content"] != "[generated image]"

        payload["content"][0]["text"] = "mutated"
        payload["tool_calls"][0]["id"] = "mutated"
        payload["reasoning_content"] = "mutated"
        captured.tool_calls[0]["id"] = "still-original"
        assert captured.to_dict()["content"][0]["text"] == "hello"
        assert original["tool_calls"][0]["id"] == "c1"
        assert captured.extra_fields["reasoning_content"] == "think"

    async def test_no_router_does_not_emit(self):
        controller = _controller(router=None)
        controller._completed_assistant_message = _assistant_message(
            controller.conversation
        )

        await end_assistant(controller)

    async def test_no_captured_completion_does_not_emit(self):
        recorder = OutputRecorder()
        controller = _controller(router=recorder)
        controller._completed_assistant_message = None

        await end_assistant(controller)

        assert recorder.events == []


class TestRunOnceAssistantEvents:
    async def test_text_round_emits_start_then_saved_message(self):
        recorder = OutputRecorder()
        controller = _controller(router=recorder, script=["plain reply"])
        controller.llm.last_assistant_content_parts = [
            ImagePart(url="https://example.invalid/gen.png")
        ]
        controller.llm.last_assistant_extra_fields = {"reasoning_content": "plan"}
        await controller.push_event(create_user_input_event("hi"))

        async for _ in controller.run_once():
            pass

        assert _event_types(recorder) == ["assistant_round_start", "assistant_message"]
        payload = _message_payload(recorder)
        assert payload["content"][0]["text"] == "plain reply"
        assert payload["content"][1]["image_url"]["url"] == (
            "https://example.invalid/gen.png"
        )
        assert payload["reasoning_content"] == "plan"
        stored = controller.conversation.get_last_assistant_message()
        assert stored is controller._completed_assistant_message
        payload["reasoning_content"] = "mutated"
        assert stored.extra_fields["reasoning_content"] == "plan"

    async def test_native_round_saves_tool_calls_not_placeholder(self):
        recorder = OutputRecorder()
        controller = _controller(router=recorder, script=["calling"], native=True)

        class _Call:
            id = "c1"
            name = "echo"
            arguments = '{"msg": "hi"}'

            def parsed_arguments(self):
                return {"msg": "hi"}

        controller.llm.last_tool_calls = [_Call()]
        controller.llm.last_assistant_extra_fields = {"reasoning_content": "use echo"}
        await controller.push_event(create_user_input_event("hi"))

        async for _ in controller.run_once():
            pass

        assert _event_types(recorder) == ["assistant_round_start", "assistant_message"]
        payload = _message_payload(recorder)
        assert payload["content"] == "calling"
        assert payload["tool_calls"][0]["id"] == "c1"
        assert payload["tool_calls"][0]["function"]["name"] == "echo"
        assert payload["reasoning_content"] == "use echo"
        payload["tool_calls"][0]["id"] = "mutated"
        stored = controller._completed_assistant_message
        assert stored.tool_calls[0]["id"] == "c1"

    async def test_no_events_skips_completion_and_does_not_emit(self):
        recorder = OutputRecorder()
        controller = _controller(router=recorder)

        stream = controller.run_once()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(stream), 0.01)
        await stream.aclose()
        assert recorder.events == []
        assert getattr(controller, "_completed_assistant_message", None) is None

    async def test_no_router_completes_without_emit(self):
        controller = _controller(router=None, script=["ok"])
        await controller.push_event(create_user_input_event("hi"))

        async for _ in controller.run_once():
            pass

        stored = controller.conversation.get_last_assistant_message()
        assert stored is not None
        assert stored.get_text_content() == "ok"
