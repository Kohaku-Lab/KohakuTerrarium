import json

import pytest

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.llm.antigravity_stream import read_events, Turn


async def lines(values):
    for value in values:
        yield value


@pytest.mark.asyncio
async def test_sse_frames_comments_multiline_and_done():
    result = [
        event
        async for event in read_events(
            lines(
                [
                    ": keepalive",
                    "data: {",
                    'data: "response": {}}',
                    "",
                    "data: [DONE]",
                    "",
                ]
            )
        )
    ]
    assert result == [{"response": {}}]


@pytest.mark.asyncio
async def test_incomplete_and_oversized_sse_fail():
    for values in (["data: {"], ["data: " + "x" * 1_048_577, ""]):
        with pytest.raises(AntigravityError):
            _ = [event async for event in read_events(lines(values))]


def test_turn_preserves_signature_only_and_thinking_and_cumulative_usage():
    turn = Turn()

    def event(parts, **candidate):
        return {
            "response": {
                "candidates": [{"content": {"parts": parts}, **candidate}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            }
        }

    assert (
        turn.feed(
            event([{"text": "reason", "thought": True}, {"thoughtSignature": "sig"}])
        )
        == []
    )
    assert turn.committed
    assert turn.feed(event([{"text": "answer"}], finishReason="STOP")) == ["answer"]
    turn.finish()
    assert turn.parts[1] == {"thoughtSignature": "sig"}
    assert turn.usage["completion_tokens"] == 5
    assert turn.finish_reason == "stop"
    assert turn.extra_fields()["_kt_assistant_segments"][0]["text"] == "reason"


def test_tools_are_distinct_and_unfinished_turn_fails():
    turn = Turn()
    turn.feed(
        {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "echo", "args": {"n": n}}}
                                for n in (1, 2)
                            ]
                        }
                    }
                ]
            }
        }
    )
    assert len({call.id for call in turn.calls}) == 2
    assert [json.loads(call.arguments) for call in turn.calls] == [{"n": 1}, {"n": 2}]
    with pytest.raises(AntigravityError, match="incomplete_stream"):
        turn.finish()


@pytest.mark.parametrize(
    "response",
    [{"candidates": value} for value in (None, False, 1, "private-response", {})]
    + [{"candidates": [value]} for value in (None, False, 1, "private-response", [])]
    + [
        {"candidates": [{"content": value}]}
        for value in (None, False, 1, "private-response", [])
    ]
    + [
        {"candidates": [{"content": {"parts": value}}]}
        for value in (None, False, 1, "private-response", {})
    ]
    + [{"candidates": [{"finishReason": value}]} for value in (["STOP"], {"STOP": 1})],
)
def test_malformed_response_structure_raises_redacted_error(response):
    with pytest.raises(AntigravityError, match="^Antigravity: malformed_response$"):
        Turn().feed({"response": response})


def test_optional_empty_and_usage_only_frames_remain_valid():
    turn = Turn()
    for response in ({}, {"candidates": []}, {"usageMetadata": {"totalTokenCount": 7}}):
        assert turn.feed({"response": response}) == []
    assert turn.feed({"candidates": [{"finishReason": "STOP"}]}) == []
    turn.finish()
    assert turn.usage == {"total_tokens": 7}
    assert turn.finish_reason == "stop"
