"""Codex HTTP stream outcomes through the real SDK with an offline transport."""

import asyncio
import json

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from kohakuterrarium.errors import ResponseStreamError
from kohakuterrarium.llm.codex.provider import CodexOAuthProvider

pytestmark = pytest.mark.real_llm_provider


@pytest.mark.parametrize(
    "fault",
    [
        "read_error",
        "read_timeout",
        "write_error",
        "write_timeout",
        "protocol",
        "unknown_connection",
    ],
)
async def test_ambiguous_submission_before_headers_is_not_retried(fault):
    requests = []

    async def respond(request):
        requests.append(await request.aread())
        errors = {
            "read_error": httpx.ReadError,
            "read_timeout": httpx.ReadTimeout,
            "write_error": httpx.WriteError,
            "write_timeout": httpx.WriteTimeout,
            "protocol": httpx.RemoteProtocolError,
        }
        if fault == "unknown_connection":
            raise APIConnectionError(request=request)
        raise errors[fault]("connection lost", request=request)

    provider = CodexOAuthProvider(
        model="test",
        api_key="test",
        base_url="https://test.invalid/v1",
        retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        with pytest.raises(ResponseStreamError) as caught:
            async for _ in provider.chat([{"role": "user", "content": "draw"}]):
                pass
        assert caught.value.code == "response_unconfirmed"
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "fault", ["connect", "connect_timeout", "pool_timeout", "rate_limit"]
)
async def test_proven_unsubmitted_or_rejected_request_can_recover(fault):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            if fault == "rate_limit":
                return httpx.Response(429, json={"error": {"message": "rate limited"}})
            error = {
                "connect": httpx.ConnectError,
                "connect_timeout": httpx.ConnectTimeout,
                "pool_timeout": httpx.PoolTimeout,
            }[fault]
            raise error("not submitted", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='data: {"type":"response.output_text.delta","delta":"Done"}\n\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n',
        )

    provider = CodexOAuthProvider(
        model="test",
        api_key="test",
        base_url="https://test.invalid/v1",
        retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        assert [
            part async for part in provider.chat([{"role": "user", "content": "draw"}])
        ] == ["Done"]
        assert len(requests) == 2
    finally:
        await provider.close()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("fault", ["json", "utf8", "decode", "unknown"])
async def test_accepted_stream_decode_failure_never_resubmits(partial, fault):
    requests = []

    class BrokenFrames(httpx.AsyncByteStream):
        async def __aiter__(self):
            if partial:
                yield b'data: {"type":"response.output_text.delta","delta":"Partial"}\n\n'
            if fault == "json":
                yield b"data: {not valid json}\n\n"
            elif fault == "utf8":
                yield b"data: \xff\xfe\n\n"
            elif fault == "decode":
                raise httpx.DecodingError("invalid encoding")
            else:
                raise ValueError("unknown stream failure")

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=BrokenFrames()
        )

    provider = CodexOAuthProvider(
        model="test",
        api_key="test",
        base_url="https://test.invalid/v1",
        retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    chunks = []
    try:
        with pytest.raises(ResponseStreamError) as caught:
            async for part in provider.chat([{"role": "user", "content": "draw"}]):
                chunks.append(part)
        assert caught.value.code == "response_unconfirmed"
        assert chunks == (["Partial"] if partial else [])
        assert len(requests) == 1
    finally:
        await provider.close()


class _BrokenBody(httpx.AsyncByteStream):
    def __init__(self, completed, close_error, partial, transport_error):
        self.completed = completed
        self.close_error = close_error
        self.partial = partial
        self.transport_error = transport_error

    async def __aiter__(self):
        if self.partial:
            yield b'data: {"type":"response.output_text.delta","delta":"Partial"}\n\n'
        if self.completed:
            yield b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        if not self.close_error:
            request = httpx.Request("POST", "https://test.invalid/v1/responses")
            error = httpx.RemoteProtocolError("incomplete chunked read")
            if self.transport_error == "sdk_connection":
                raise APIConnectionError(request=request) from error
            if self.transport_error == "sdk_timeout":
                raise APITimeoutError(request=request) from error
            raise error
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        if self.close_error:
            raise TimeoutError("cleanup timed out")


class _WaitingBody(httpx.AsyncByteStream):
    def __init__(self, close_error):
        self.waiting = asyncio.Event()
        self.closed = False
        self.close_error = close_error

    async def __aiter__(self):
        yield b'data: {"type":"response.output_text.delta","delta":"Partial"}\n\n'
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True
        if self.close_error:
            raise TimeoutError("cleanup timed out")


@pytest.mark.parametrize("close_error", [False, True])
async def test_cancellation_remains_cancellation(close_error):
    body = _WaitingBody(close_error)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=body
        )

    provider = CodexOAuthProvider(
        model="test", api_key="test", base_url="https://test.invalid/v1"
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        base_url="https://test.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    chunks = []

    async def collect():
        async for part in provider.chat([{"role": "user", "content": "draw"}]):
            chunks.append(part)

    task = asyncio.create_task(collect())
    try:
        await body.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
        assert chunks == ["Partial"]
        assert len(requests) == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.close()


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("close_error", [False, True])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("transport_error", ["raw", "sdk_connection", "sdk_timeout"])
async def test_transport_and_cleanup_failures_never_resubmit(
    completed, close_error, partial, transport_error
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_BrokenBody(completed, close_error, partial, transport_error),
        )

    provider = CodexOAuthProvider(
        model="test",
        api_key="test",
        base_url="https://test.invalid/v1",
        retry_policy={"max_retries": 1, "base_delay": 0, "jitter": 0},
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        base_url="https://test.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    chunks = []
    try:
        if completed:
            chunks = [
                part
                async for part in provider.chat([{"role": "user", "content": "draw"}])
            ]
        else:
            with pytest.raises(ResponseStreamError) as caught:
                async for part in provider.chat([{"role": "user", "content": "draw"}]):
                    chunks.append(part)
            assert caught.value.code == "response_unconfirmed"
        assert chunks == (["Partial"] if partial else [])
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "terminal,code",
    [
        (None, "response_unconfirmed"),
        (
            {"type": "response.failed", "response": {"status": "failed"}},
            "response_failed",
        ),
        (
            {"type": "response.incomplete", "response": {"status": "incomplete"}},
            "response_incomplete",
        ),
        ({"type": "error", "message": "secret upstream payload"}, "response_failed"),
    ],
)
@pytest.mark.parametrize("partial", [False, True])
async def test_unsuccessful_stream_preserves_text_without_resubmitting(
    terminal, code, partial
):
    requests = []
    events = (
        [{"type": "response.output_text.delta", "delta": "Partial answer"}]
        if partial
        else []
    )
    if terminal:
        events.append(terminal)
    body = (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    )

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=body
        )

    provider = CodexOAuthProvider(
        model="test", api_key="test", base_url="https://test.invalid/v1"
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        base_url="https://test.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    chunks = []
    try:
        with pytest.raises(ResponseStreamError) as caught:
            async for chunk in provider.chat([{"role": "user", "content": "Draw"}]):
                chunks.append(chunk)
        assert getattr(caught.value, "code", None) == code
        assert "secret upstream payload" not in str(caught.value)
        assert chunks == (["Partial answer"] if partial else [])
        assert len(requests) == 1
        assert provider.last_usage == {}
        assert provider.last_tool_calls == []
    finally:
        await provider.close()


async def test_completed_without_usage_is_not_a_failure():
    body = 'data: {"type":"response.output_text.delta","delta":"Done"}\n\ndata: {"type":"response.completed","response":{"id":"r1","status":"completed"}}\n\ndata: [DONE]\n\n'
    provider = CodexOAuthProvider(
        model="test", api_key="test", base_url="https://test.invalid/v1"
    )
    provider._client = AsyncOpenAI(
        api_key="test",
        base_url="https://test.invalid/v1",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, text=body
                )
            )
        ),
    )
    try:
        assert [
            part async for part in provider.chat([{"role": "user", "content": "Hello"}])
        ] == ["Done"]
        assert provider.last_usage == {}
    finally:
        await provider.close()
