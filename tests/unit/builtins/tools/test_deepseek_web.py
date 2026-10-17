"""Direct Search/Fetch contracts at the HTTP boundary."""

import asyncio
import json

import httpx
import pytest

from kohakuterrarium.builtins.tools import deepseek_web
from kohakuterrarium.builtins.tools.deepseek_web import (
    DeepSeekWebClient,
    DeepSeekWebError,
    DeepSeekWebTransientError,
    bounded_text,
    render_search,
    validate_url,
)


def search_data(**overrides):
    return {
        "results": [
            {
                "title": "Source",
                "url": "https://example.com/article",
                "summary": "Long summary",
                "snippet": "Short snippet",
                "content": "Page body",
                "date": None,
                "site_name": None,
                "authority": -1,
                "rerank_score": 0.7,
                **overrides,
            }
        ]
    }


@pytest.fixture
def http_boundary(monkeypatch):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(deepseek_web, "get_api_key", lambda provider: "test-secret")
    monkeypatch.setattr(deepseek_web, "_NEXT_REQUEST", {})

    def install(handler):
        monkeypatch.setattr(
            deepseek_web.httpx,
            "AsyncClient",
            lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw),
        )

    return install


async def test_search_payload_and_output(http_boundary):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=search_data())

    http_boundary(handle)
    data = await DeepSeekWebClient().search(
        "中文 site:example.com",
        50,
        need_content=True,
        ori_question="原始问题",
        localization="203.0.113.1",
    )
    assert calls == [
        {
            "query": "中文 site:example.com",
            "count": 50,
            "need_content": True,
            "ori_question": "原始问题",
            "localization": "203.0.113.1",
        }
    ]
    result = render_search(data, "query", 50, True)
    assert "Long summary" in result.output and "Page body" in result.output
    assert "Short snippet" not in result.output
    assert result.metadata["citation_status"] == "search_results"
    assert result.metadata["results"][0]["authority"] == -1
    assert "verified_sources" not in result.metadata
    assert "usage" not in result.metadata and "model" not in result.metadata
    assert "test-secret" not in repr(result)


async def test_fetch_payload_and_empty_title(http_boundary):
    def handle(request):
        assert str(request.url).endswith("/fetch")
        assert json.loads(request.content) == {"url": "https://example.com"}
        return httpx.Response(200, json={"title": "", "content": "# Page"})

    http_boundary(handle)
    assert await DeepSeekWebClient().fetch("https://example.com") == {
        "title": "",
        "content": "# Page",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "x" * 129},
        {"query": None},
        {"count": 0},
        {"count": 51},
        {"count": True},
        {"count": 1.5},
        {"need_content": "false"},
        {"ori_question": " "},
        {"ori_question": "x" * 129},
        {"localization": "us-en"},
        {"localization": 123},
    ],
)
async def test_invalid_search_never_sends(http_boundary, kwargs):
    def handle(request):
        pytest.fail("invalid request reached HTTP")

    http_boundary(handle)
    with pytest.raises(DeepSeekWebError):
        await DeepSeekWebClient().search(**{"query": "valid", **kwargs})


@pytest.mark.parametrize(
    "url",
    [
        None,
        12,
        "",
        "ftp://example.com",
        "https:///",
        "https://u:p@example.com",
        "https://example.com:bad",
        "https://[bad",
        "https://example.com/ a",
        "https://example.com/" + "x" * 8192,
    ],
)
def test_invalid_urls(url):
    with pytest.raises(DeepSeekWebError):
        validate_url(url)


@pytest.mark.parametrize(
    "status,detail",
    [
        (400, "invalid request"),
        (401, "key"),
        (402, "balance"),
        (403, "security policy"),
        (404, "no usable"),
        (413, "too large"),
        (302, "rejected"),
    ],
)
async def test_terminal_errors_do_not_retry_or_echo_body(http_boundary, status, detail):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": {"message": "test-secret"}},
            headers={"Location": "https://unrelated.example"},
        )

    http_boundary(handle)
    with pytest.raises(DeepSeekWebError, match=detail) as caught:
        await DeepSeekWebClient().fetch("https://example.com")
    assert not isinstance(caught.value, DeepSeekWebTransientError)
    assert "test-secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 504, "transport"])
async def test_transient_failure_has_bounded_retries(http_boundary, status):
    calls = []

    def handle(request):
        calls.append(request)
        if status == "transport":
            raise httpx.ConnectError("test-secret", request=request)
        return httpx.Response(status, headers={"Retry-After": "not-a-number"})

    http_boundary(handle)
    with pytest.raises(DeepSeekWebTransientError) as caught:
        await DeepSeekWebClient().search("query")
    assert len(calls) == 3
    assert "test-secret" not in str(caught.value)


async def test_retry_recovers_and_empty_results_are_success(http_boundary):
    replies = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"results": []}),
    ]
    http_boundary(lambda request: replies.pop(0))
    data = await DeepSeekWebClient().search("query")
    assert render_search(data, "query", 10, False).sources == []
    assert "No results found" in render_search(data, "query", 10, False).output
    assert replies == []


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"results": {}},
        {"results": [None]},
        search_data(url="javascript:alert(1)"),
        search_data(summary=None),
    ],
)
async def test_malformed_search_is_not_empty_success(http_boundary, data):
    http_boundary(lambda request: httpx.Response(200, json=data))
    with pytest.raises(DeepSeekWebError):
        await DeepSeekWebClient().search("query")


@pytest.mark.parametrize(
    "data", [{}, {"title": None, "content": "body"}, {"title": "Page", "content": " "}]
)
async def test_invalid_fetch_is_not_success(http_boundary, data):
    http_boundary(lambda request: httpx.Response(200, json=data))
    with pytest.raises(DeepSeekWebError):
        await DeepSeekWebClient().fetch("https://example.com")


async def test_invalid_json_is_terminal(http_boundary):
    http_boundary(lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(DeepSeekWebError, match="invalid JSON"):
        await DeepSeekWebClient().search("query")


async def test_missing_key_never_sends(http_boundary, monkeypatch):
    http_boundary(lambda request: pytest.fail("missing key reached HTTP"))
    monkeypatch.setattr(deepseek_web, "get_api_key", lambda provider: "")
    with pytest.raises(DeepSeekWebError, match="key is not configured"):
        await DeepSeekWebClient().search("query")


async def test_cancel_and_deadline_release_request(http_boundary, monkeypatch):
    entered, released = asyncio.Event(), asyncio.Event()

    async def handle(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    http_boundary(handle)
    task = asyncio.create_task(DeepSeekWebClient().fetch("https://example.com"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
    monkeypatch.setattr(deepseek_web, "REQUEST_TIMEOUT", 0.01)
    with pytest.raises(DeepSeekWebTransientError, match="timed out"):
        await DeepSeekWebClient().fetch("https://example.com")


def test_pacing_is_shared_per_endpoint(monkeypatch):
    monkeypatch.setattr(deepseek_web, "_NEXT_REQUEST", {})
    monkeypatch.setattr(deepseek_web.time, "monotonic", lambda: 10.0)
    assert deepseek_web._pace_delay("search") == 0
    assert deepseek_web._pace_delay("search") == pytest.approx(0.21)
    assert deepseek_web._pace_delay("fetch") == 0
    monkeypatch.setattr(deepseek_web.time, "monotonic", lambda: 11.0)
    assert deepseek_web._pace_delay("search") == 0


def test_summary_fallback_and_bounds():
    data = search_data(summary="", content="x" * 20000)
    result = render_search(data, "query", 10, False)
    assert "Short snippet" in result.output and "xxx" not in result.output
    result = render_search(data, "query", 10, True)
    assert result.metadata["truncated"] and "[truncated]" in result.output
    data["results"] *= 50
    result = render_search(data, "query", 50, True)
    assert len(result.output) <= 100000 and result.output.endswith("[truncated]")
    assert bounded_text("abcdef", 3) == "\n[t"
    assert bounded_text("abc", 3) == "abc"
