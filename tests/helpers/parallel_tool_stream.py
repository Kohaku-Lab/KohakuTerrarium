"""Recorded Gemini parallel-call responses over an in-memory HTTP transport."""

import json
from pathlib import Path

import httpx

from kohakuterrarium.llm.openai.provider import OpenAIProvider

FIXTURE = Path(__file__).parents[1] / "unit/llm/fixtures/gemini_parallel_deltas.json"
RECORDED_CALLS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _sse(delta):
    return (
        "data: "
        + json.dumps(
            {
                "id": "parallel",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "delta": delta}],
            }
        )
        + "\n\n"
    )


async def parallel_tool_provider(requests):
    """Return a real provider with recorded tool calls then history-checked replies."""

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = "".join(_sse({"tool_calls": [call]}) for call in RECORDED_CALLS)
        else:
            messages = body["messages"]
            assistant = next(m for m in messages if m.get("tool_calls"))
            assert assistant["tool_calls"] == RECORDED_CALLS
            results = {
                m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"
            }
            assert set(results) == {c["id"] for c in RECORDED_CALLS}
            assert "TASK.txt" in results[RECORDED_CALLS[0]["id"]]
            assert "notes.md" in results[RECORDED_CALLS[1]["id"]]
            assert "PARALLEL_OK" in results[RECORDED_CALLS[2]["id"]]
            assert "_kt_tool_call_extra" not in assistant
            payload = _sse({"content": "PARALLEL_OK"})
        return httpx.Response(
            200,
            text=payload + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAIProvider(
        api_key="test-key",
        model="test",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        max_retries=0,
    )
    previous = provider._client
    provider._client = previous.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    await previous.close()
    return provider
