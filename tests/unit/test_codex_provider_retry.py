"""Regression: Codex provider retries on transient stream errors.

Before the fix, ``CodexOAuthProvider._stream_chat`` had no retry loop —
when the upstream raised ``RemoteProtocolError`` mid-stream, the
exception bubbled straight to ``agent_handlers`` and surfaced as a
fatal turn error. OpenAI / Anthropic providers already had retry
loops; Codex was the gap.
"""

import httpx
import pytest

from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.recovery import RetryPolicy


def _make_provider(monkeypatch, attempts_to_fail: int, exc_factory):
    """Build a CodexOAuthProvider whose ``_raw_stream_chat`` fails N
    times then yields a successful chunk on attempt N+1.
    """
    provider = CodexOAuthProvider.__new__(CodexOAuthProvider)
    # Minimal init for retry behaviour — we don't touch the SDK.
    from kohakuterrarium.llm.base import BaseLLMProvider, LLMConfig

    BaseLLMProvider.__init__(provider, LLMConfig(model="codex-test"))
    provider._retry_policy = RetryPolicy(
        max_retries=3, base_delay=0, max_delay=0, jitter=0.0
    )
    provider.attempt_count = 0

    async def fake_raw_stream(messages, **kwargs):
        provider.attempt_count += 1
        if provider.attempt_count <= attempts_to_fail:
            raise exc_factory()
        yield "ok"

    provider._raw_stream_chat = fake_raw_stream  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_codex_retries_on_remote_protocol_error(monkeypatch):
    """``peer closed connection`` is the canonical truncated-stream
    error. The retry classifier marks it TRANSIENT; the provider's
    retry loop must catch and retry it.
    """
    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=2,
        exc_factory=lambda: httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        ),
    )

    chunks: list[str] = []
    async for chunk in provider._stream_chat([]):
        chunks.append(chunk)

    assert chunks == ["ok"]
    assert provider.attempt_count == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_codex_retries_on_no_status_error(monkeypatch):
    """A network exception with no status_code (DNS reset, TCP cut)
    classifies as TRANSIENT and must retry.
    """

    class _NetErr(Exception):
        pass

    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=1,
        exc_factory=lambda: _NetErr("connection reset by peer"),
    )

    chunks: list[str] = []
    async for chunk in provider._stream_chat([]):
        chunks.append(chunk)

    assert chunks == ["ok"]
    assert provider.attempt_count == 2


@pytest.mark.asyncio
async def test_codex_retries_on_5xx(monkeypatch):
    class _ServerErr(Exception):
        def __init__(self, msg, status):
            super().__init__(msg)
            self.status_code = status

    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=1,
        exc_factory=lambda: _ServerErr("server hiccup", 503),
    )

    chunks: list[str] = []
    async for chunk in provider._stream_chat([]):
        chunks.append(chunk)

    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_codex_retries_on_429(monkeypatch):
    class _RateErr(Exception):
        def __init__(self, msg, status):
            super().__init__(msg)
            self.status_code = status

    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=1,
        exc_factory=lambda: _RateErr("Too Many Requests", 429),
    )

    chunks: list[str] = []
    async for chunk in provider._stream_chat([]):
        chunks.append(chunk)

    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_codex_does_not_retry_on_explicit_4xx(monkeypatch):
    """Explicit 4xx (USER_ERROR class) must propagate — those are
    real client mistakes that retrying won't fix (auth, malformed
    request, etc.).
    """

    class _BadReq(Exception):
        def __init__(self, msg, status):
            super().__init__(msg)
            self.status_code = status

    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=10,  # would retry forever if classified as retryable
        exc_factory=lambda: _BadReq("Bad Request", 400),
    )

    with pytest.raises(_BadReq):
        async for _ in provider._stream_chat([]):
            pass

    # Only one attempt — no retry loop entered for USER_ERROR.
    assert provider.attempt_count == 1


@pytest.mark.asyncio
async def test_codex_gives_up_after_max_retries(monkeypatch):
    """Persistent transient errors eventually escape so the agent
    can surface a real failure instead of looping forever.
    """
    provider = _make_provider(
        monkeypatch,
        attempts_to_fail=999,
        exc_factory=lambda: httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        ),
    )

    with pytest.raises(httpx.RemoteProtocolError):
        async for _ in provider._stream_chat([]):
            pass

    # Initial attempt + max_retries = 1 + 3 = 4 attempts before giving up.
    assert provider.attempt_count == 4
