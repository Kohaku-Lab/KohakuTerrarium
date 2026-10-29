"""Parallel streamed calls through the real OpenAI SDK and history serializer."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from kohakuterrarium.llm.message import AssistantMessage, ToolMessage, UserMessage
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.openai_helpers import TOOL_CALL_EXTRA_KEY
from kohakuterrarium.llm.turn_segments import KT_ASSISTANT_SEGMENTS

pytestmark = pytest.mark.real_llm_provider

FIXTURE = Path(__file__).parent / "fixtures" / "gemini_parallel_deltas.json"
CALLS = json.loads(FIXTURE.read_text(encoding="utf-8"))
URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _sequence(shape):
    calls = deepcopy(CALLS)
    if shape == "gemini":
        return [[call] for call in calls]
    if shape == "shared-index":
        return [[{**call, "index": 0}] for call in calls]
    if shape == "null-index":
        return [[{**call, "index": None}] for call in calls]
    if shape == "id-interleaved":
        return [
            [{**call, "index": 0, "function": {**call["function"], "arguments": ""}}]
            for call in calls
        ] + [
            [
                {
                    "id": call["id"],
                    "index": 0,
                    "function": {"arguments": call["function"]["arguments"]},
                }
            ]
            for call in reversed(calls)
        ]
    if shape == "indexless-sequential":
        return [
            batch
            for call in calls
            for batch in (
                [{**call, "function": {**call["function"], "arguments": ""}}],
                [{"function": {"arguments": call["function"]["arguments"]}}],
            )
        ]
    indexes = {} if shape == "indexless-batch" else {"index": 0}
    headers = []
    tails = []
    for index, call in enumerate(calls):
        fields = {"index": index} if indexes else {}
        headers.append(
            {
                **call,
                **fields,
                "function": {
                    **call["function"],
                    "arguments": call["function"]["arguments"][:5],
                },
            }
        )
        tails.append(
            {**fields, "function": {"arguments": call["function"]["arguments"][5:]}}
        )
    if shape == "openai":
        return [headers, *[[tail] for tail in reversed(tails)]]
    return [headers, tails]


def _sse(batches):
    chunks = [
        {"reasoning_content": "Inspect files."},
        *[{"tool_calls": batch} for batch in batches],
    ]
    return (
        "".join(
            "data: "
            + json.dumps(
                {
                    "id": "response",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "test",
                    "choices": [{"index": 0, "delta": delta}],
                }
            )
            + "\n\n"
            for delta in chunks
        )
        + "data: [DONE]\n\n"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "openai",
        "gemini",
        "shared-index",
        "null-index",
        "id-interleaved",
        "indexless-sequential",
        "indexless-batch",
    ],
)
async def test_parallel_stream_preserves_arguments_signatures_and_history(shape):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=_sse(_sequence(shape)),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "id": "next",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with OpenAIProvider(api_key="test", model="test", base_url=URL) as provider:
        previous = provider._client
        provider._client = previous.with_options(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        )
        await previous.close()
        messages = [UserMessage("Inspect files in parallel.")]
        assert [piece async for piece in provider.chat(messages)] == []
        calls = provider.last_tool_calls
        assert [(c.id, c.name, c.arguments) for c in calls] == [
            (c["id"], c["function"]["name"], c["function"]["arguments"]) for c in CALLS
        ]
        assert [c.parsed_arguments() for c in calls] == [
            {"pattern": "*.txt"},
            {"pattern": "*.md"},
            {"path": "TASK.txt"},
        ]
        extras = provider.last_assistant_extra_fields
        assert extras[TOOL_CALL_EXTRA_KEY] == {
            CALLS[0]["id"]: CALLS[0]["extra_content"]
        }
        assert extras[KT_ASSISTANT_SEGMENTS] == [
            {
                "type": "reasoning",
                "source": "reasoning_content",
                "text": "Inspect files.",
            },
            *[{"type": "tool_call_ref", "call_id": c["id"]} for c in CALLS],
        ]
        messages.append(
            AssistantMessage(
                "",
                tool_calls=[
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.arguments},
                    }
                    for c in calls
                ],
                extra_fields=extras,
            )
        )
        messages.extend(
            ToolMessage(f"result-{i}", tool_call_id=c.id) for i, c in enumerate(calls)
        )
        assert (await provider.chat_complete(messages)).content == "done"
        history = requests[1]["messages"]
        assert history[1]["tool_calls"] == CALLS
        assert [m["tool_call_id"] for m in history[2:]] == [c["id"] for c in CALLS]
        assert [m["content"] for m in history[2:]] == [
            "result-0",
            "result-1",
            "result-2",
        ]
        assert TOOL_CALL_EXTRA_KEY not in history[1]
        assert KT_ASSISTANT_SEGMENTS not in history[1]
