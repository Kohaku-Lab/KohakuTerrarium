"""A real ``OpenAIProvider`` whose HTTP layer streams dual-form reasoning.

Some OpenAI-compatible endpoints send the same chain of thought on every delta
twice: as plain ``reasoning_content`` and as structured ``reasoning_details``.
The provider, the agent, the session store and the API all run for real; only
the HTTP transport is in memory.
"""

import json
from collections.abc import Sequence
from typing import Any

import httpx

from kohakuterrarium.llm.openai import OpenAIProvider

REASONING_PIECES = ("Let me ", "think ", "about it")
ANSWER = "The answer is 42."
DETAIL_KEY = "0:reasoning.text"


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def dual_form_sse(pieces: Sequence[str], answer: str) -> str:
    """SSE body: each piece arrives as reasoning_content AND reasoning_details."""
    body = [_chunk({"role": "assistant", "content": ""})]
    for piece in pieces:
        body.append(
            _chunk(
                {
                    "reasoning_content": piece,
                    "reasoning_details": [
                        {"type": "reasoning.text", "index": 0, "text": piece}
                    ],
                }
            )
        )
    body.append(_chunk({"content": answer}))
    body.append(_chunk({}, finish="stop"))
    body.append("data: [DONE]\n\n")
    return "".join(body)


def dual_form_provider(
    pieces: Sequence[str] = REASONING_PIECES,
    answer: str = ANSWER,
    requests: list[dict[str, Any]] | None = None,
) -> OpenAIProvider:
    """Build an ``OpenAIProvider`` that always answers with a dual-form stream.

    Every request body is appended to ``requests`` when given.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=dual_form_sse(pieces, answer),
        )

    provider = OpenAIProvider(api_key="test-key", model="test", max_retries=0)
    provider._client = provider._client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    return provider


def reasoning_segments(segments: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in segments if s.get("type") == "reasoning"]
