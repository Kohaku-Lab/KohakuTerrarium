"""Unit tests for ``llm/anthropic_provider.py`` request building and retry behaviour.

The SDK 1.x ``messages.create`` no longer accepts ``temperature``, ``top_p`` or
``top_k`` keyword arguments, so they must leave the provider inside
``extra_body`` and only for models that accept them.
"""

import httpx
import pytest

from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
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


class TestMidStreamFailure:
    def _provider(self, attempts):
        provider = anthropic_provider(
            "claude-sonnet-4-6",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0, jitter=0),
        )

        async def raw_stream(messages, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                yield "Hel"
            raise httpx.ReadError("connection dropped mid-stream")

        provider._raw_stream_chat = raw_stream
        return provider

    async def test_text_already_delivered_is_not_replayed_by_a_retry(self):
        attempts = []
        provider = self._provider(attempts)
        received = []
        with pytest.raises(httpx.ReadError):
            async for chunk in provider._stream_chat(MESSAGES):
                received.append(chunk)
        assert received == ["Hel"]
        assert len(attempts) == 1

    async def test_failure_before_any_text_is_still_retried(self):
        attempts = []
        provider = self._provider(attempts)
        attempts.append(1)
        with pytest.raises(httpx.ReadError):
            async for _ in provider._stream_chat(MESSAGES):
                pass
        assert len(attempts) == 4
