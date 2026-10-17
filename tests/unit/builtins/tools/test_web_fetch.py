"""Fetch backend selection, bounded content, and fallback boundaries."""

import asyncio

import httpx
import pytest

from kohakuterrarium.builtins.tools import deepseek_web, web_fetch
from kohakuterrarium.builtins.tools.web_fetch import WebFetchTool
from kohakuterrarium.modules.tool.base import ToolConfig


@pytest.fixture
def fetch_http(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(deepseek_web, "get_api_key", lambda provider: "test-secret")
    monkeypatch.setattr(deepseek_web, "_NEXT_REQUEST", {})

    def install(handler):
        monkeypatch.setattr(
            deepseek_web.httpx,
            "AsyncClient",
            lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
        )

    return install


async def test_deepseek_fetch_truncates_and_records_provenance(fetch_http):
    fetch_http(
        lambda request: httpx.Response(
            200, json={"title": "Article", "content": "# Page\n" + "x" * 200}
        )
    )
    tool = WebFetchTool(ToolConfig(extra={"backend": "deepseek"}))
    result = await tool._execute({"url": "example.com", "max_length": 30})
    assert result.success
    assert len(result.output) == 30 and result.output.endswith("[truncated]")
    assert result.metadata["session_metadata"]["title"] == "Article"
    assert result.metadata["session_metadata"]["backend"] == "deepseek"
    assert result.metadata["url"] == "https://example.com"
    assert "test-secret" not in repr(result)


async def test_default_preserves_auto_chain(monkeypatch):
    called = []

    async def crawl(url):
        called.append("crawl4ai")
        raise web_fetch._SkipBackend

    async def extract(url):
        called.append("trafilatura")
        return "page text"

    monkeypatch.setattr(web_fetch, "_fetch_crawl4ai", crawl)
    monkeypatch.setattr(web_fetch, "_fetch_trafilatura", extract)
    result = await WebFetchTool()._execute({"url": "https://example.com"})
    assert result.output == "page text"
    assert called == ["crawl4ai", "trafilatura"]
    assert result.metadata["requested_backend"] == "auto"
    assert result.metadata["backend"] == "trafilatura"


@pytest.mark.parametrize("status", [401, 402, 403, 404, 400])
async def test_terminal_errors_never_fallback(fetch_http, monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    async def unexpected(url):
        pytest.fail("terminal failure fell back")

    fetch_http(handle)
    monkeypatch.setattr(web_fetch, "_fetch_crawl4ai", unexpected)
    tool = WebFetchTool(ToolConfig(extra={"backend": "deepseek", "fallback": "auto"}))
    result = await tool._execute({"url": "https://example.com"})
    assert str(status) in result.error
    assert len(calls) == 1


@pytest.mark.parametrize("fallback", ["none", "auto"])
async def test_transient_fallback_is_explicit(fetch_http, monkeypatch, fallback):
    fetch_http(lambda request: httpx.Response(503))
    monkeypatch.setattr(deepseek_web, "MAX_ATTEMPTS", 1)
    called = []

    async def crawl(url):
        called.append(url)
        return "Recovered page"

    monkeypatch.setattr(web_fetch, "_fetch_crawl4ai", crawl)
    result = await WebFetchTool(
        ToolConfig(extra={"backend": "deepseek", "fallback": fallback})
    )._execute({"url": "https://example.com"})
    if fallback == "none":
        assert "503" in result.error and called == []
    else:
        assert result.output == "Recovered page"
        assert result.metadata["fallback_from"] == "deepseek"
        assert result.metadata["backend"] == "crawl4ai"
        assert called == ["https://example.com"]


@pytest.mark.parametrize(
    "args",
    [
        {"url": ""},
        {"url": 1},
        {"url": "ftp://example.com"},
        {"url": "https://user:pass@example.com"},
        {"url": "example.com", "max_length": 0},
        {"url": "example.com", "max_length": True},
        {"url": "example.com", "max_length": 100001},
    ],
)
async def test_invalid_args_fail_before_network(fetch_http, args):
    fetch_http(lambda request: pytest.fail("invalid input reached HTTP"))
    assert not (
        await WebFetchTool(ToolConfig(extra={"backend": "deepseek"}))._execute(args)
    ).success


async def test_cancel_never_triggers_fallback(fetch_http, monkeypatch):
    started = asyncio.Event()

    async def handle(request):
        started.set()
        await asyncio.Event().wait()

    async def unexpected(url):
        pytest.fail("cancel triggered fallback")

    fetch_http(handle)
    monkeypatch.setattr(web_fetch, "_fetch_crawl4ai", unexpected)
    tool = WebFetchTool(ToolConfig(extra={"backend": "deepseek", "fallback": "auto"}))
    task = asyncio.create_task(tool._execute({"url": "https://example.com"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_missing_key_disables_deepseek(monkeypatch):
    monkeypatch.setattr(web_fetch, "has_api_key", lambda provider: False)
    tool = WebFetchTool()
    assert tool.backend == "auto"
    with pytest.raises(ValueError, match="key is not configured"):
        tool.validate_runtime_options({"backend": "deepseek"})
