"""OpenAI provider recovery through the real SDK and an in-memory HTTP transport."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

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


class TestOpenAIRetries:
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

        monkeypatch.setattr(provider_module, "asyncio", SimpleNamespace(sleep=sleep))
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
