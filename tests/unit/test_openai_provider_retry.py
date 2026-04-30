"""OpenAI provider retry and emergency-drop loop tests."""

import pytest

from kohakuterrarium.llm.base import BaseLLMProvider, ChatResponse, LLMConfig
from kohakuterrarium.llm.openai_sanitize import strip_surrogates
from kohakuterrarium.llm.recovery import RetryPolicy


class _ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class _RetryProvider(BaseLLMProvider):
    def __init__(self, outcomes):
        super().__init__(LLMConfig(model="retry-test"))
        self._retry_policy = RetryPolicy(max_retries=2, base_delay=0, jitter=0)
        self.outcomes = list(outcomes)
        self.raw_calls = 0

    async def _raw_stream_chat(
        self, messages, *, tools=None, provider_native_tools=None, **kwargs
    ):
        self.raw_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        yield outcome

    async def _raw_complete_chat(self, messages, **kwargs):
        self.raw_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return ChatResponse(content=outcome, finish_reason="stop", usage={}, model="m")


@pytest.mark.asyncio
async def test_streaming_retries_rate_limit_then_succeeds(monkeypatch):
    from kohakuterrarium.llm.openai import OpenAIProvider

    monkeypatch.setattr(
        "kohakuterrarium.llm.openai.asyncio.sleep", lambda delay: _noop()
    )
    provider = _RetryProvider([_ProviderError("rate", 429), "ok"])

    chunks = []
    async for chunk in OpenAIProvider._stream_chat(
        provider, [{"role": "user", "content": "x"}]
    ):
        chunks.append(chunk)

    assert chunks == ["ok"]
    assert provider.raw_calls == 2


@pytest.mark.asyncio
async def test_complete_retries_rate_limit_then_succeeds(monkeypatch):
    from kohakuterrarium.llm.openai import OpenAIProvider

    monkeypatch.setattr(
        "kohakuterrarium.llm.openai.asyncio.sleep", lambda delay: _noop()
    )
    provider = _RetryProvider([_ProviderError("rate", 429), "ok"])

    response = await OpenAIProvider._complete_chat(
        provider, [{"role": "user", "content": "x"}]
    )

    assert response.content == "ok"
    assert provider.raw_calls == 2


@pytest.mark.asyncio
async def test_overflow_drops_tool_round_and_notifies_callback(monkeypatch):
    from kohakuterrarium.llm.openai import OpenAIProvider

    provider = _RetryProvider(
        [
            _ProviderError(
                "overflow",
                400,
                {"error": {"code": "context_length_exceeded"}},
            ),
            "recovered",
        ]
    )
    seen = []
    provider.on_emergency_drop(seen.append)
    messages = [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read"}}],
        },
        {"role": "tool", "name": "read", "content": "x" * 100},
    ]

    chunks = []
    async for chunk in OpenAIProvider._stream_chat(provider, messages):
        chunks.append(chunk)

    assert chunks == ["recovered"]
    assert provider.raw_calls == 2
    assert len(seen) == 1
    assert "tool-result truncated" in seen[0][1]["content"]


@pytest.mark.asyncio
async def test_user_error_does_not_retry(monkeypatch):
    from kohakuterrarium.llm.openai import OpenAIProvider

    provider = _RetryProvider([_ProviderError("bad", 400)])

    with pytest.raises(_ProviderError):
        async for _ in OpenAIProvider._stream_chat(
            provider, [{"role": "user", "content": "x"}]
        ):
            pass
    assert provider.raw_calls == 1


@pytest.mark.asyncio
async def test_second_overflow_after_drop_surfaces_without_loop():
    from kohakuterrarium.llm.openai import OpenAIProvider

    overflow = _ProviderError(
        "overflow",
        400,
        {"error": {"code": "context_length_exceeded"}},
    )
    provider = _RetryProvider([overflow, overflow])
    messages = [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read"}}],
        },
        {"role": "tool", "name": "read", "content": "x" * 100},
    ]

    with pytest.raises(_ProviderError):
        async for _ in OpenAIProvider._stream_chat(provider, messages):
            pass
    assert provider.raw_calls == 2


async def _noop():
    return None


def test_strip_surrogates_removes_invalid_codepoints():
    assert strip_surrogates("safe\udcaftext") == "safetext"
