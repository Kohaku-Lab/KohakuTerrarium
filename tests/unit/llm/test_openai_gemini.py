"""Gemini OpenAI-compat wire shape through the real SDK and an in-memory transport."""

import json

import httpx
import pytest

from kohakuterrarium.bootstrap.llm import _create_from_profile
from kohakuterrarium.llm import presets as presets_mod
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.openai_helpers import TOOL_CALL_EXTRA_KEY
from kohakuterrarium.llm.profiles import get_profile

pytestmark = pytest.mark.real_llm_provider

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
SIGNATURE = {"google": {"thought_signature": "opaque-signature"}}


@pytest.fixture(autouse=True)
def gemini_key(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(presets_mod, "list_packages", lambda: [])
    presets_mod._all_presets_cache = None
    presets_mod._all_presets_key = None
    yield
    presets_mod._all_presets_cache = None
    presets_mod._all_presets_key = None


async def _attach(provider, respond):
    previous = provider._client
    provider._client = previous.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    await previous.close()


def _tool_call_response(streaming):
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "echo", "arguments": "{}"},
        "extra_content": SIGNATURE,
    }
    if streaming:
        chunk = {
            "id": "r",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gemini-4-argon",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [{"index": 0, **call}],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        return httpx.Response(
            200,
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
    return httpx.Response(
        200,
        json={
            "id": "r",
            "object": "chat.completion",
            "created": 0,
            "model": "gemini-4-argon",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    )


def _text_response():
    return httpx.Response(
        200,
        json={
            "id": "r",
            "object": "chat.completion",
            "created": 0,
            "model": "gemini-4-argon",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


@pytest.mark.parametrize(
    "selector,level",
    [
        ("gemini/gemini-4-argon", "HIGH"),
        ("gemini/gemini-4-argon@thinking=low", "LOW"),
        ("gemini/gemini-3.8-flash@thinking=medium", "MEDIUM"),
        ("gemini/gemini-3.5-flash-lite", "MEDIUM"),
        ("gemini/gemini-3.5-flash-lite@thinking=minimal", "MINIMAL"),
    ],
)
async def test_thinking_level_is_nested_under_body_extra_body(selector, level):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return _text_response()

    provider = _create_from_profile(get_profile(selector))
    assert isinstance(provider, OpenAIProvider)
    await _attach(provider, respond)
    await provider.chat_complete([{"role": "user", "content": "hi"}])
    body = bodies[0]
    assert "google" not in body
    assert body["extra_body"] == {
        "google": {"thinking_config": {"thinking_level": level}}
    }


@pytest.mark.parametrize("streaming", [False, True])
async def test_tool_call_signature_round_trips_on_gemini(streaming):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return _tool_call_response(streaming) if len(bodies) == 1 else _text_response()

    provider = OpenAIProvider(api_key="k", base_url=GEMINI_URL, model="gemini-4-argon")
    await _attach(provider, respond)
    messages = [{"role": "user", "content": "echo"}]
    if streaming:
        assert "".join([c async for c in provider.chat(messages, stream=True)]) == ""
    else:
        await provider.chat_complete(messages)
    call = provider.last_tool_calls[0]
    assert provider.last_assistant_extra_fields[TOOL_CALL_EXTRA_KEY] == {
        "call-1": SIGNATURE
    }
    messages += [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
            ],
            **provider.last_assistant_extra_fields,
        },
        {"role": "tool", "tool_call_id": call.id, "content": "ok"},
    ]
    await provider.chat_complete(messages)
    assistant = bodies[1]["messages"][1]
    assert assistant["tool_calls"][0]["extra_content"] == SIGNATURE
    assert TOOL_CALL_EXTRA_KEY not in assistant


async def test_signature_state_never_reaches_other_endpoints():
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return _text_response()

    provider = OpenAIProvider(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-4-argon",
    )
    await _attach(provider, respond)
    await provider.chat_complete(
        [
            {"role": "user", "content": "echo"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
                TOOL_CALL_EXTRA_KEY: {"call-1": SIGNATURE},
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]
    )
    assistant = bodies[0]["messages"][1]
    assert TOOL_CALL_EXTRA_KEY not in assistant
    assert "extra_content" not in assistant["tool_calls"][0]


async def test_calls_without_extra_content_record_no_state():
    def respond(request):
        return _text_response()

    provider = OpenAIProvider(api_key="k", base_url=GEMINI_URL, model="gemini-4-argon")
    await _attach(provider, respond)
    await provider.chat_complete([{"role": "user", "content": "hi"}])
    assert TOOL_CALL_EXTRA_KEY not in provider.last_assistant_extra_fields
