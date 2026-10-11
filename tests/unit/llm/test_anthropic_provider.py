"""Unit tests for ``llm/anthropic_provider.py`` request building and retry behaviour.

The SDK 1.x ``messages.create`` no longer accepts ``temperature``, ``top_p`` or
``top_k`` keyword arguments, so they must leave the provider inside
``extra_body`` and only for models that accept them.
"""

import base64
import io
import json
import random

import httpx
import pytest
from PIL import Image

from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
from kohakuterrarium.llm.base import ChatResponse
from kohakuterrarium.llm.recovery import RetryPolicy
from tests.helpers.anthropic_stream import ANSWER, anthropic_provider

MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
]
SAMPLING_KEYS = {"temperature", "top_p", "top_k"}


def build(model="claude-sonnet-4-6", **provider_kwargs):
    provider = AnthropicProvider(api_key="k", model=model, **provider_kwargs)
    return provider._build_create_kwargs


class TestSamplingReachesTheWireOnlyThroughExtraBody:
    def test_accepting_model_carries_sampling_in_extra_body(self):
        create = build(temperature=0.7, extra_body={"top_p": 0.9, "top_k": 5})(
            MESSAGES, stream=True
        )
        assert not SAMPLING_KEYS & set(create)
        assert create["extra_body"] == {"temperature": 0.7, "top_p": 0.9, "top_k": 5}

    def test_per_call_temperature_beats_the_provider_default(self):
        create = build(temperature=0.7)(MESSAGES, stream=False, temperature=0.2)
        assert create["extra_body"] == {"temperature": 0.2}

    def test_extra_body_temperature_is_moved_not_duplicated(self):
        create = build(extra_body={"temperature": 0.4})(MESSAGES, stream=True)
        assert "temperature" not in create
        assert create["extra_body"] == {"temperature": 0.4}

    @pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-5-5"])
    def test_rejecting_model_gets_no_sampling_but_keeps_other_extra_body(self, model):
        create = build(
            model, temperature=0.7, extra_body={"top_p": 0.9, "custom_flag": True}
        )(MESSAGES, stream=True)
        assert not SAMPLING_KEYS & set(create)
        assert create["extra_body"] == {"custom_flag": True}

    def test_no_sampling_configured_leaves_extra_body_out(self):
        create = build()(MESSAGES, stream=True)
        assert not SAMPLING_KEYS & set(create)
        assert "extra_body" not in create

    def test_known_body_fields_still_travel_as_keyword_arguments(self):
        create = build(extra_body={"top_p": 0.9, "service_tier": "auto"})(
            MESSAGES, stream=True
        )
        assert create["service_tier"] == "auto"
        assert create["extra_body"] == {"top_p": 0.9}


class TestProgrammingErrorsAreNotRetried:
    async def test_type_error_from_the_sdk_fails_on_the_first_attempt(self):
        provider = anthropic_provider(
            "claude-sonnet-4-6",
            retry_policy=RetryPolicy(max_retries=3, base_delay=0, jitter=0),
        )
        calls = 0

        async def broken(**kwargs):
            nonlocal calls
            calls += 1
            raise TypeError("create() got an unexpected keyword argument 'x'")

        provider._client.messages.create = broken
        with pytest.raises(TypeError):
            await provider.chat_complete(MESSAGES)
        assert calls == 1

    async def test_working_transport_returns_the_answer(self):
        provider = anthropic_provider("claude-sonnet-4-6", temperature=0.3)
        assert (await provider.chat_complete(MESSAGES)).content == ANSWER


class _ImageRejected(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("messages.0.content.1.image.source.base64: image exceeds 5 MB")


class TestContentRejection:
    async def test_stream_and_complete_drop_the_refused_image(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
        messages = [{"role": "user", "content": [{"type": "text", "text": "x"}, image]}]
        for streaming in (True, False):
            provider = anthropic_provider("claude-sonnet-4-6")
            seen = []
            repairs = []
            provider.on_context_repair(repairs.append)

            async def raw_stream(current, **kwargs):
                seen.append(current)
                if len(seen) == 1:
                    raise _ImageRejected()
                yield "ok"

            async def raw_complete(current, **kwargs):
                seen.append(current)
                if len(seen) == 1:
                    raise _ImageRejected()
                return ChatResponse(
                    content="ok", finish_reason="stop", usage={}, model="m"
                )

            provider._raw_stream_chat = raw_stream
            provider._raw_complete_chat = raw_complete
            if streaming:
                text = "".join([c async for c in provider._stream_chat(messages)])
            else:
                text = (await provider._complete_chat(messages)).content
            assert text == "ok" and len(seen) == 2
            assert [p["type"] for p in seen[1][0]["content"]] == ["text", "text"]
            assert "image exceeds 5 MB" in seen[1][0]["content"][1]["text"]
            assert len(repairs) == 1


class _RequestTooLarge(Exception):
    status_code = 413

    def __init__(self):
        super().__init__(
            "Error code: 413 - {'type': 'error', 'error': {'type': "
            "'request_too_large', 'message': 'Request exceeds the maximum allowed "
            "number of bytes.'}}"
        )


def _noise_block(seed):
    pixels = random.Random(seed).randbytes(700 * 700 * 3)
    stream = io.BytesIO()
    Image.frombytes("RGB", (700, 700), pixels).save(stream, "PNG")
    data = base64.b64encode(stream.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


class TestOversizeRejection:
    async def test_413_refits_images_under_a_learned_ceiling(self):
        limit = 1_200_000
        provider = anthropic_provider("claude-sonnet-4-6")
        create = provider._client.messages.create
        sizes = []

        async def guarded(**kwargs):
            size = len(json.dumps(kwargs["messages"]).encode())
            sizes.append(size)
            if size > limit:
                raise _RequestTooLarge()
            return await create(**kwargs)

        provider._client.messages.create = guarded
        repairs = []
        provider.on_context_repair(repairs.append)
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "scans"}]
                + [_noise_block(seed) for seed in range(3)],
            }
        ]
        assert (await provider.chat_complete(messages)).content == ANSWER
        assert len(sizes) >= 2 and sizes[0] > limit >= sizes[-1]
        assert repairs == []
        assert provider.request_ceiling().max_bytes < sizes[0]
        assert provider.with_model("claude-opus-4-8").request_ceiling() is (
            provider.request_ceiling()
        )

    def test_request_max_bytes_is_a_framework_knob_not_a_wire_field(self):
        create = build(extra_body={"request_max_bytes": 2_000_000})(
            MESSAGES, stream=True
        )
        assert "extra_body" not in create
        provider = AnthropicProvider(
            api_key="k", model="m", extra_body={"request_max_bytes": 0}
        )
        assert provider._request_byte_target() is None
        assert AnthropicProvider(api_key="k", model="m")._request_max_bytes == (
            28_000_000
        )


class TestMidStreamFailure:
    def _provider(self, attempts, retry_reply):
        provider = anthropic_provider(
            "claude-sonnet-4-6",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0, jitter=0),
        )

        async def raw_stream(messages, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                yield "Hel"
                raise httpx.ReadError("connection dropped mid-stream")
            yield retry_reply

        provider._raw_stream_chat = raw_stream
        return provider

    async def _received(self, retry_reply):
        attempts = []
        provider = self._provider(attempts, retry_reply)
        received = [chunk async for chunk in provider._stream_chat(MESSAGES)]
        return "".join(received), len(attempts)

    async def test_retry_after_a_dropped_stream_does_not_repeat_delivered_text(self):
        assert await self._received("Hello") == ("Hello", 2)

    async def test_retry_that_words_the_reply_differently_is_still_delivered(self):
        assert await self._received("Sure thing") == ("HelSure thing", 2)

    async def test_failure_before_any_text_is_retried_until_the_budget_ends(self):
        attempts = []
        provider = self._provider(attempts, "unused")

        async def always_fails(messages, **kwargs):
            attempts.append(1)
            raise httpx.ReadError("connection refused")
            yield

        provider._raw_stream_chat = always_fails
        with pytest.raises(httpx.ReadError):
            async for _ in provider._stream_chat(MESSAGES):
                pass
        assert len(attempts) == 3
