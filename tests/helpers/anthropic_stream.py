"""A real ``AnthropicProvider`` whose HTTP layer is an in-memory ``httpx2`` transport.

The Anthropic SDK 1.x runs on ``httpx2``. The provider, the SDK client and the
request builder run for real; only the network is replaced. Every request body
is captured so tests can assert on what would have reached the API.
"""

import json
from typing import Any

import httpx2

from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
from kohakuterrarium.llm.recovery import RetryPolicy

ANSWER = "The answer is 42."

MESSAGE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-test",
    "content": [{"type": "text", "text": ANSWER}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 3, "output_tokens": 5},
}


def _sse(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def stream_body(answer: str = ANSWER) -> str:
    return _sse(
        {
            "type": "message_start",
            "message": {**MESSAGE, "content": [], "stop_reason": None},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": answer},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    )


def anthropic_provider(
    model: str,
    requests: list[dict[str, Any]] | None = None,
    answer: str = ANSWER,
    **provider_kwargs: Any,
) -> AnthropicProvider:
    """Build an ``AnthropicProvider`` that answers every request with ``answer``.

    Request bodies are appended to ``requests`` when given.
    """

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if requests is not None:
            requests.append(body)
        if body.get("stream"):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=stream_body(answer),
            )
        message = dict(MESSAGE, content=[{"type": "text", "text": answer}])
        return httpx2.Response(200, json=message)

    provider_kwargs.setdefault("retry_policy", RetryPolicy(max_retries=0))
    provider = AnthropicProvider(api_key="test-key", model=model, **provider_kwargs)
    provider._client = provider._client.with_options(
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    )
    return provider
