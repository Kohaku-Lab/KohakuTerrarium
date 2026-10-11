"""OpenAI provider recovery through the real SDK and an in-memory HTTP transport."""

import asyncio
import base64
import io
import json
import random
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from PIL import Image

from kohakuterrarium.llm import openai as provider_module
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.recovery import RetryPolicy

MESSAGES = [{"role": "user", "content": "inspect the image"}]
LOCAL_MEDIA_ERROR = "Cannot load local files without --allowed-local-media-path"


async def _attach_transport(provider, respond):
    previous = provider._client
    provider._client = previous.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    await previous.close()


async def _turn(provider, streaming):
    if streaming:
        return "".join([chunk async for chunk in provider.chat(MESSAGES)])
    return (await provider.chat_complete(MESSAGES)).content


def _error(status, message):
    return httpx.Response(
        status,
        json={"error": {"message": message, "type": "provider_error", "code": status}},
        headers={"retry-after-ms": "1"},
    )


def _success(streaming):
    if streaming:
        chunk = {
            "id": "reply",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [
                {"index": 0, "delta": {"content": "ok"}, "finish_reason": None}
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
            "id": "reply",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        },
    )


class TestContentRejection:
    @pytest.mark.parametrize("streaming", [False, True])
    async def test_rejected_tool_image_is_removed_and_the_model_told(self, streaming):
        requests = []

        async def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return _error(400, "Invalid image: the image could not be decoded")
            return _success(streaming)

        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": [image]},
        ]
        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            repairs = []
            provider.on_context_repair(repairs.append)
            if streaming:
                text = "".join([c async for c in provider.chat(messages)])
            else:
                text = (await provider.chat_complete(messages)).content
        assert text == "ok"
        assert len(requests) == 2
        assert "image_url" in json.dumps(requests[0]["messages"])
        retried = requests[1]["messages"][-1]["content"]
        assert "image_url" not in json.dumps(retried)
        assert "could not be decoded" in json.dumps(retried)
        assert [(r.kind, r.scope) for r in repairs] == [("strip_media", "newest")]

    async def test_unrelated_bad_request_is_raised_untouched(self):
        requests = []

        async def respond(request):
            requests.append(request)
            return _error(400, "Unrecognized request argument supplied: foo")

        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            repairs = []
            provider.on_context_repair(repairs.append)
            with pytest.raises(APIStatusError):
                await provider.chat_complete(MESSAGES)
        assert len(requests) == 1 and repairs == []


def _noise_image_part(seed):
    pixels = random.Random(seed).randbytes(700 * 700 * 3)
    stream = io.BytesIO()
    Image.frombytes("RGB", (700, 700), pixels).save(stream, "PNG")
    url = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": url}}


def _proxy_413():
    return httpx.Response(
        413,
        text="<html><head><title>413 Request Entity Too Large</title></head></html>",
        headers={"content-type": "text/html"},
    )


def _image_tool_round(parts):
    return [
        {"role": "user", "content": "read the scans"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": parts},
    ]


class TestOversizeRejection:
    @pytest.mark.parametrize("streaming", [False, True])
    async def test_proxy_413_lowers_the_ceiling_and_keeps_the_images(self, streaming):
        limit = 1_200_000
        sizes = []

        async def respond(request):
            sizes.append(len(request.content))
            return _proxy_413() if len(request.content) > limit else _success(streaming)

        messages = _image_tool_round([_noise_image_part(seed) for seed in range(3)])
        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            repairs, drops = [], []
            provider.on_context_repair(repairs.append)
            provider.on_emergency_drop(drops.append)
            if streaming:
                text = "".join([c async for c in provider.chat(messages)])
            else:
                text = (await provider.chat_complete(messages)).content
            ceiling = provider.request_ceiling().max_bytes
            sibling = provider.with_model("other")
            fork = provider.fork()
        assert text == "ok"
        assert len(sizes) >= 2 and sizes[0] > limit >= sizes[-1]
        assert sizes == sorted(sizes, reverse=True)
        assert repairs == [] and drops == []
        assert ceiling is not None and ceiling < sizes[0]
        assert sibling.request_ceiling() is provider.request_ceiling()
        assert fork.request_ceiling() is provider.request_ceiling()

    async def test_learned_ceiling_applies_to_the_next_turn_up_front(self):
        limit = 1_200_000
        sizes = []

        async def respond(request):
            sizes.append(len(request.content))
            return _proxy_413() if len(request.content) > limit else _success(False)

        messages = _image_tool_round([_noise_image_part(seed) for seed in range(3)])
        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            await provider.chat_complete(messages)
            first_turn = len(sizes)
            await provider.chat_complete(messages)
        assert first_turn >= 2
        assert len(sizes) == first_turn + 1 and sizes[-1] <= limit

    async def test_text_only_413_drops_the_tool_round_with_a_note(self):
        requests = []

        async def respond(request):
            requests.append(json.loads(request.content))
            return _proxy_413() if len(requests) == 1 else _success(False)

        messages = _image_tool_round([{"type": "text", "text": "x" * 5000}])
        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            repairs = []
            provider.on_context_repair(repairs.append)
            text = (await provider.chat_complete(messages)).content
            ceiling = provider.request_ceiling().max_bytes
        assert text == "ok" and len(requests) == 2
        assert ceiling is None
        assert [(r.kind, r.scope) for r in repairs] == [("drop_tool_round", "newest")]
        retried = json.dumps(requests[1]["messages"])
        assert "x" * 5000 not in retried
        assert "413 Request Entity Too Large" in retried

    async def test_endless_413_is_bounded_then_raised(self):
        requests = []

        async def respond(request):
            requests.append(request)
            return _proxy_413()

        messages = _image_tool_round([_noise_image_part(seed) for seed in range(3)])
        async with OpenAIProvider(api_key="test", model="test") as provider:
            await _attach_transport(provider, respond)
            with pytest.raises(APIStatusError):
                await provider.chat_complete(messages)
        # 1 original + 3 shrinks + 3 content stages; drop stages find nothing left.
        assert len(requests) <= 1 + 3 + 3 + 1


class TestOpenAIRetries:
    @pytest.mark.parametrize("streaming", [False, True])
    async def test_socket_options_are_removed_from_http_requests(self, streaming):
        requests = []

        async def respond(request):
            requests.append(json.loads(request.content))
            return _success(streaming)

        async with OpenAIProvider(
            api_key="test",
            model="test",
            extra_body={"websocket_connection_options": {"ping_timeout": None}},
        ) as provider:
            await _attach_transport(provider, respond)
            chunks = [
                chunk
                async for chunk in provider.chat(
                    MESSAGES,
                    stream=streaming,
                    extra_body={"websocket_connection_options": {"max_size": 1}},
                )
            ]
            assert chunks == ["ok"]
        assert len(requests) == 1
        assert "websocket_connection_options" not in requests[0]

    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize(
        ("header", "expected_delay"),
        [("10", 10.0), ("Thu, 01 Jan 10000 00:00:00 GMT", 1.0)],
    )
    async def test_retry_after_is_observed(
        self, streaming, header, expected_delay, monkeypatch
    ):
        delays = []
        requests = []

        async def sleep(delay):
            delays.append(delay)

        async def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    429,
                    json={"error": {"message": "busy"}},
                    headers={"retry-after": header},
                )
            return _success(streaming)

        monkeypatch.setattr(
            provider_module,
            "asyncio",
            SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread),
        )
        async with OpenAIProvider(
            api_key="test-key", model="test", retry_policy=RetryPolicy(jitter=0)
        ) as provider:
            await _attach_transport(provider, respond)
            assert await _turn(provider, streaming) == "ok"
            assert len(requests) == 2
            assert delays == [expected_delay]

    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("local_media", [False, True])
    async def test_positive_retry_hint_respects_local_media_errors(
        self, streaming, local_media
    ):
        requests = []

        async def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    500 if local_media else 422,
                    json={
                        "error": {
                            "message": LOCAL_MEDIA_ERROR if local_media else "busy"
                        }
                    },
                    headers={"x-should-retry": "true", "retry-after-ms": "1"},
                )
            return _success(streaming)

        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            retry_policy=RetryPolicy(max_retries=1, base_delay=0, jitter=0),
        ) as provider:
            await _attach_transport(provider, respond)
            if local_media:
                with pytest.raises(APIStatusError, match="allowed-local-media-path"):
                    await _turn(provider, streaming)
                assert len(requests) == 1
            else:
                assert await _turn(provider, streaming) == "ok"
                assert len(requests) == 2

    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("variant", ["initial", "model", "credentials"])
    async def test_local_media_failure_sends_once(
        self, streaming, variant, monkeypatch
    ):
        requests = []

        async def respond(request):
            requests.append(request)
            return _error(500, LOCAL_MEDIA_ERROR)

        original = OpenAIProvider(api_key="test-key", model="test")
        provider = original
        try:
            if variant == "model":
                provider = original.with_model("other-model")
            elif variant == "credentials":
                monkeypatch.setenv("OPENAI_API_KEY", "rotated-test-key")
                provider._credential_provider = "openai"
                assert provider.reload_credentials()
            await _attach_transport(provider, respond)
            with pytest.raises(APIStatusError, match="allowed-local-media-path"):
                await _turn(provider, streaming)
            assert len(requests) == 1
            if variant == "credentials":
                assert requests[0].headers["authorization"] == "Bearer rotated-test-key"
            if variant == "model":
                assert json.loads(requests[0].content)["model"] == "other-model"
        finally:
            await provider.close()
            if provider is not original:
                await original.close()

    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("status", [408, 409, 429, 503])
    @pytest.mark.parametrize("budget", [0, 2])
    async def test_retry_policy_bounds_http_attempts(self, streaming, status, budget):
        requests = []

        async def respond(request):
            requests.append(request.content)
            return _error(status, "provider unavailable")

        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            retry_policy=RetryPolicy(max_retries=budget, base_delay=0, jitter=0),
        ) as provider:
            await _attach_transport(provider, respond)
            with pytest.raises(APIStatusError):
                await _turn(provider, streaming)
            assert len(requests) == budget + 1
            assert len(set(requests)) == 1

    @pytest.mark.parametrize("streaming", [False, True])
    @pytest.mark.parametrize("status", [408, 409, 503])
    async def test_transient_failure_can_recover(self, streaming, status):
        requests = []

        async def respond(request):
            requests.append(request.content)
            return (
                _error(status, "temporary outage")
                if len(requests) == 1
                else _success(streaming)
            )

        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            retry_policy=RetryPolicy(max_retries=1, base_delay=0, jitter=0),
        ) as provider:
            await _attach_transport(provider, respond)
            assert await _turn(provider, streaming) == "ok"
            assert len(requests) == 2

    async def test_legacy_zero_retries_disables_retry(self):
        requests = []

        async def respond(request):
            requests.append(request)
            return _error(503, "temporary outage")

        async with OpenAIProvider(
            api_key="test-key", model="test", max_retries=0
        ) as provider:
            await _attach_transport(provider, respond)
            with pytest.raises(APIStatusError):
                await provider.chat_complete(MESSAGES)
            assert len(requests) == 1

    async def test_explicit_policy_overrides_legacy_zero_retries(self):
        requests = []

        async def respond(request):
            requests.append(request)
            return _error(503, "temporary outage")

        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            max_retries=0,
            retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
        ) as provider:
            await _attach_transport(provider, respond)
            with pytest.raises(APIStatusError):
                await provider.chat_complete(MESSAGES)
            assert len(requests) == 2


class _DropAfterFirstChunk(httpx.AsyncByteStream):
    """SSE body that sends one text chunk, then the connection breaks."""

    async def __aiter__(self):
        chunk = {
            "id": "reply",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [
                {"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        raise httpx.ReadError("connection dropped mid-stream")


def _reply_with(text):
    chunk = {
        "id": "reply",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return httpx.Response(
        200,
        text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )


class TestMidStreamFailure:
    async def _received_after_drop(self, retry_reply):
        requests = []

        async def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    stream=_DropAfterFirstChunk(),
                    headers={"content-type": "text/event-stream"},
                )
            return _reply_with(retry_reply)

        received = []
        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0, jitter=0),
        ) as provider:
            await _attach_transport(provider, respond)
            async for chunk in provider.chat(MESSAGES):
                received.append(chunk)
        return "".join(received), len(requests)

    async def test_retry_after_a_dropped_stream_does_not_repeat_delivered_text(self):
        text, attempts = await self._received_after_drop("Hello")
        assert text == "Hello"
        assert attempts == 2

    async def test_retry_that_words_the_reply_differently_is_still_delivered(self):
        text, attempts = await self._received_after_drop("Sure thing")
        assert text == "HelSure thing"
        assert attempts == 2

    async def test_failure_before_any_text_is_still_retried(self):
        requests = []

        async def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return _error(503, "temporary outage")
            return _success(True)

        async with OpenAIProvider(
            api_key="test-key",
            model="test",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0, jitter=0),
        ) as provider:
            await _attach_transport(provider, respond)
            assert await _turn(provider, True) == "ok"
        assert len(requests) == 2
