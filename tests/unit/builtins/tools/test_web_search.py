"""Unit tests for web-search parsing, backend selection, and fallback.

Network calls are replaced with fixed HTML or deterministic fakes.
"""

import pytest

from kohakuterrarium.builtins.tools import web_search
from kohakuterrarium.builtins.tools.web_search import (
    DeepSeekSearchBackend,
    DuckDuckGoSearchBackend,
    SearchBackendOperationalError,
    SearchBackendUnavailable,
    WebSearchTool,
    _normalize_deepseek_response,
    _parse_ddg_html,
    _search_session_metadata,
    _unwrap_ddg_redirect,
)
from kohakuterrarium.modules.tool.base import ToolConfig

# Minimal DDG HTML response — three results, the third has the
# redirector wrapper around its href so the unwrap path is covered.
_DDG_SAMPLE_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="https://example.com/first">First &amp; only</a>
  <a class="result__snippet" href="...">First snippet body.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.org/second">Second result</a>
  <a class="result__snippet" href="...">Second <b>HTML</b> snippet.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.example%2Fpath%3Fq%3D1&amp;rut=abc">Redirector-wrapped</a>
  <a class="result__snippet" href="...">Third snippet.</a>
</div>
</body></html>
"""


def test_session_metadata_preserves_codex_grounding_sources():
    source = {"title": "Weather", "url": "https://example.com/weather"}
    metadata = _search_session_metadata(
        {
            "backend": "codex",
            "citation_status": "grounded",
            "source_count": 1,
            "verified_source_count": 0,
            "action_source_count": 1,
            "verified_sources": [],
            "consulted_sources": [source],
        }
    )

    assert metadata["citation_status"] == "grounded"
    assert metadata["source_count"] == 1
    assert metadata["consulted_sources"] == [source]


def test_repeat_search_warning_is_backend_specific_guidance():
    # The description is canonical and must match the doc file, so
    # backend-specific advice moved to the tool-guidance slot.
    codex_tool = WebSearchTool(ToolConfig(extra={"backend": "codex"}))

    assert "once per question" in codex_tool.prompt_contribution()
    for backend in ("duckduckgo", "deepseek"):
        tool = WebSearchTool(ToolConfig(extra={"backend": backend}))
        assert tool.prompt_contribution() is None
        assert "once per question" not in tool.description


def test_codex_login_does_not_change_the_default_backend(monkeypatch):
    monkeypatch.setattr(web_search, "codex_search_available", lambda: True)

    tool = WebSearchTool()

    assert tool.backend == "duckduckgo"
    assert isinstance(tool._backend(), DuckDuckGoSearchBackend)


class TestParseDdgHtml:
    def test_extracts_title_href_snippet(self):
        results = _parse_ddg_html(_DDG_SAMPLE_HTML, max_results=10)
        assert len(results) == 3
        assert results[0]["href"] == "https://example.com/first"
        assert results[0]["title"] == "First & only"
        assert results[0]["body"] == "First snippet body."

    def test_strips_inline_html_from_snippet(self):
        results = _parse_ddg_html(_DDG_SAMPLE_HTML, max_results=10)
        # ``<b>HTML</b>`` inside the snippet is stripped to plain text.
        assert results[1]["body"] == "Second HTML snippet."

    def test_unwraps_ddg_redirector(self):
        results = _parse_ddg_html(_DDG_SAMPLE_HTML, max_results=10)
        # The third result's href was ``//duckduckgo.com/l/?uddg=…``
        # — parser must hand back the real target URL, not the
        # tracker.
        assert results[2]["href"] == "https://target.example/path?q=1"

    def test_caps_at_max_results(self):
        results = _parse_ddg_html(_DDG_SAMPLE_HTML, max_results=2)
        assert len(results) == 2

    def test_empty_html_returns_empty(self):
        assert _parse_ddg_html("<html></html>", max_results=5) == []

    def test_unwrap_helper_passes_through_normal_url(self):
        assert _unwrap_ddg_redirect("https://example.com") == "https://example.com"

    def test_unwrap_helper_handles_redirector_with_extra_params(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.b%2Fc&rut=xyz&s=1"
        assert _unwrap_ddg_redirect(href) == "https://a.b/c"


class TestWebSearchFallback:
    @pytest.mark.asyncio
    async def test_uses_ddgs_when_available_and_working(self, monkeypatch):
        # When ddgs returns results, the httpx fallback must NOT
        # fire — pin so a future bug that always falls through
        # (e.g. accidentally inverted condition) gets caught.
        async def _fake_ddg(query, max_results, region):
            return [
                {
                    "href": "https://from-ddgs.example/x",
                    "title": "from ddgs",
                    "body": "ddgs body",
                }
            ]

        called: list[str] = []

        async def _fake_httpx(query, max_results, region):
            called.append("httpx")
            return [{"href": "wrong", "title": "wrong", "body": "wrong"}]

        monkeypatch.setattr(web_search, "_has_ddg", lambda: True)
        monkeypatch.setattr(web_search, "_search_ddg", _fake_ddg)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", _fake_httpx)

        tool = WebSearchTool()
        tool.config = ToolConfig()
        result = await tool._execute({"query": "anything"})
        assert "from ddgs" in result.output
        assert called == [], "httpx fallback should not run when ddgs succeeds"

    @pytest.mark.asyncio
    async def test_falls_back_to_httpx_when_ddgs_unavailable(self, monkeypatch):
        # Android case: ddgs absent — go straight to httpx scraper.
        async def _fake_httpx(query, max_results, region):
            return [
                {
                    "href": "https://from-httpx.example/y",
                    "title": "from httpx",
                    "body": "httpx body",
                }
            ]

        monkeypatch.setattr(web_search, "_has_ddg", lambda: False)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", _fake_httpx)

        tool = WebSearchTool()
        tool.config = ToolConfig()
        result = await tool._execute({"query": "anything"})
        assert "from httpx" in result.output

    @pytest.mark.asyncio
    async def test_falls_back_to_httpx_when_ddgs_raises(self, monkeypatch):
        # Transient ddgs failure (rate-limit, primp crash on
        # desktop, …) — fall through to httpx and serve the
        # results from there.
        async def _fake_ddg(query, max_results, region):
            raise RuntimeError("ddgs went down")

        async def _fake_httpx(query, max_results, region):
            return [
                {
                    "href": "https://fallback.example/z",
                    "title": "fallback",
                    "body": "",
                }
            ]

        monkeypatch.setattr(web_search, "_has_ddg", lambda: True)
        monkeypatch.setattr(web_search, "_search_ddg", _fake_ddg)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", _fake_httpx)

        tool = WebSearchTool()
        tool.config = ToolConfig()
        result = await tool._execute({"query": "anything"})
        assert "fallback" in result.output

    @pytest.mark.asyncio
    async def test_returns_clear_error_when_both_backends_fail(self, monkeypatch):
        async def _fake_ddg(query, max_results, region):
            raise RuntimeError("ddgs down")

        async def _fake_httpx(query, max_results, region):
            raise RuntimeError("httpx also down")

        monkeypatch.setattr(web_search, "_has_ddg", lambda: True)
        monkeypatch.setattr(web_search, "_search_ddg", _fake_ddg)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", _fake_httpx)

        tool = WebSearchTool()
        tool.config = ToolConfig()
        result = await tool._execute({"query": "anything"})
        # Prefers the ddgs error (more actionable per the source comment).
        assert result.error is not None
        assert "ddgs down" in result.error

    @pytest.mark.asyncio
    async def test_empty_query_rejected_without_calling_either_backend(
        self, monkeypatch
    ):
        called: list[str] = []

        async def _fake_ddg(*a, **kw):
            called.append("ddg")
            return []

        async def _fake_httpx(*a, **kw):
            called.append("httpx")
            return []

        monkeypatch.setattr(web_search, "_has_ddg", lambda: True)
        monkeypatch.setattr(web_search, "_search_ddg", _fake_ddg)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", _fake_httpx)

        tool = WebSearchTool()
        tool.config = ToolConfig()
        result = await tool._execute({"query": ""})
        assert result.error is not None
        assert called == []


class _Response:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class _Client:
    def __init__(self, response, capture):
        self.response = response
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.capture.append((url, kwargs))
        return self.response


class TestDeepSeekSearch:
    @pytest.mark.asyncio
    async def test_sends_forced_search_and_normalizes_sources(self, monkeypatch):
        capture = []
        data = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"type": "search", "query": "kt search"},
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Grounded answer.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/source",
                                    "title": "Source",
                                }
                            ],
                        }
                    ],
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "secret-key")
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=data), capture),
        )

        response = await DeepSeekSearchBackend("deepseek-v4-flash").search(
            "kt search", 3, "cn-zh"
        )

        _, request = capture[0]
        assert request["json"]["tools"] == [{"type": "web_search"}]
        assert request["json"]["tool_choice"] == {"type": "web_search"}
        assert request["headers"]["Authorization"] == "Bearer secret-key"
        assert response.sources == [
            {"title": "Source", "url": "https://example.com/source"}
        ]
        assert "Grounded answer" in response.output
        assert response.metadata["citation_status"] == "verified"
        assert response.metadata["annotation_count"] == 1
        assert "secret-key" not in repr(response.metadata)

    @pytest.mark.asyncio
    async def test_missing_key_does_not_use_configured_fallback(self, monkeypatch):
        called = []
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "")

        async def fake_ddg(*args):
            called.append("ddg")
            return []

        monkeypatch.setattr(web_search, "_search_httpx_ddg", fake_ddg)
        tool = WebSearchTool(
            ToolConfig(extra={"backend": "deepseek", "fallback": "duckduckgo"})
        )

        result = await tool._execute({"query": "anything"})

        assert "API key is not configured" in result.error
        assert called == []

    @pytest.mark.asyncio
    async def test_non_object_response_is_a_controlled_operational_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "configured")
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=[]), []),
        )

        with pytest.raises(SearchBackendOperationalError, match="invalid response"):
            await DeepSeekSearchBackend("deepseek-v4-flash").search("q", 3, "")

    @pytest.mark.asyncio
    async def test_failed_response_status_is_not_reported_as_empty_success(
        self, monkeypatch
    ):
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "configured")
        data = {
            "status": "failed",
            "error": {
                "code": "authentication_error",
                "message": "invalid credential",
            },
            "output": [],
        }
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=data), []),
        )

        with pytest.raises(SearchBackendUnavailable, match="authentication_error"):
            await DeepSeekSearchBackend("deepseek-v4-flash").search("q", 3, "")

    @pytest.mark.asyncio
    async def test_transient_failed_response_status_is_fallback_eligible(
        self, monkeypatch
    ):
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "configured")
        data = {
            "status": "failed",
            "error": {"code": "rate_limit_error", "message": "try later"},
            "output": [],
        }
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=data), []),
        )

        with pytest.raises(SearchBackendOperationalError, match="rate_limit_error"):
            await DeepSeekSearchBackend("deepseek-v4-flash").search("q", 3, "")

    @pytest.mark.asyncio
    async def test_incomplete_response_with_partial_output_is_explicit(
        self, monkeypatch
    ):
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "configured")
        data = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": "Partial grounded answer.",
        }
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=data), []),
        )

        response = await DeepSeekSearchBackend("deepseek-v4-flash").search("q", 3, "")

        assert "Partial grounded answer" in response.output
        assert "Incomplete DeepSeek response: max_output_tokens" in response.output
        assert response.metadata["response_status"] == "incomplete"
        assert response.metadata["incomplete_reason"] == "max_output_tokens"

    @pytest.mark.asyncio
    async def test_incomplete_response_without_output_is_not_empty_success(
        self, monkeypatch
    ):
        monkeypatch.setattr(web_search, "get_api_key", lambda provider: "configured")
        data = {
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "output": [],
        }
        monkeypatch.setattr(
            web_search.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(_Response(data=data), []),
        )

        with pytest.raises(SearchBackendUnavailable, match="content_filter"):
            await DeepSeekSearchBackend("deepseek-v4-flash").search("q", 3, "")

    @pytest.mark.asyncio
    async def test_operational_failure_uses_explicit_fallback(self, monkeypatch):
        async def fail_search(*args):
            raise SearchBackendOperationalError("rate limited")

        async def fallback_search(*args):
            return [{"href": "https://fallback", "title": "fallback", "body": ""}]

        class _FailingBackend:
            search = staticmethod(fail_search)

        monkeypatch.setattr(
            WebSearchTool,
            "_backend",
            lambda self, selected=None: _FailingBackend(),
        )
        monkeypatch.setattr(web_search, "_has_ddg", lambda: False)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", fallback_search)
        tool = WebSearchTool(
            ToolConfig(extra={"backend": "deepseek", "fallback": "duckduckgo"})
        )

        result = await tool._execute({"query": "anything"})

        assert "fallback" in result.output
        assert result.metadata["fallback_from"] == "deepseek"

    def test_schema_marks_deepseek_unavailable_without_key(self, monkeypatch):
        monkeypatch.setattr(web_search, "has_api_key", lambda provider: False)
        monkeypatch.setattr(web_search, "codex_search_available", lambda: False)

        schema = WebSearchTool().runtime_option_schema()

        assert "deepseek" in schema["backend"]["disabled_values"]
        assert "codex" in schema["backend"]["disabled_values"]
        assert schema["backend"]["default"] == "duckduckgo"
        assert schema["backend"]["values"] == ["duckduckgo", "codex", "deepseek"]

    @pytest.mark.asyncio
    async def test_explicit_codex_backend_uses_subscription_search(self, monkeypatch):
        class _CodexBackend:
            def __init__(self, model):
                self.model = model

            async def search(self, query, max_results, region):
                return web_search.SearchResponse(
                    output="codex result",
                    metadata={"backend": "codex", "model": self.model},
                )

        monkeypatch.setattr(web_search, "CodexSubscriptionSearchBackend", _CodexBackend)

        tool = WebSearchTool(ToolConfig(extra={"backend": "codex"}))
        result = await tool._execute({"query": "anything"})

        assert result.output == "codex result"
        assert result.metadata["backend"] == "codex"
        assert result.metadata["requested_backend"] == "codex"

    @pytest.mark.asyncio
    async def test_explicit_codex_operational_failure_uses_configured_fallback(
        self, monkeypatch
    ):
        async def fail_search(*args):
            raise web_search.CodexSearchOperationalError("rate limited")

        async def fallback_search(*args):
            return [{"href": "https://fallback", "title": "fallback", "body": ""}]

        class _CodexBackend:
            def __init__(self, model):
                self.search = fail_search

        monkeypatch.setattr(web_search, "CodexSubscriptionSearchBackend", _CodexBackend)
        monkeypatch.setattr(web_search, "_has_ddg", lambda: False)
        monkeypatch.setattr(web_search, "_search_httpx_ddg", fallback_search)

        tool = WebSearchTool(
            ToolConfig(extra={"backend": "codex", "fallback": "duckduckgo"})
        )
        result = await tool._execute({"query": "anything"})

        assert "fallback" in result.output
        assert result.metadata["fallback_from"] == "codex"

    @pytest.mark.asyncio
    async def test_codex_failure_without_configured_fallback_is_an_error(
        self, monkeypatch
    ):
        async def fail_search(*args):
            raise web_search.CodexSearchOperationalError("rate limited")

        class _CodexBackend:
            def __init__(self, model):
                self.search = fail_search

        monkeypatch.setattr(web_search, "CodexSubscriptionSearchBackend", _CodexBackend)
        tool = WebSearchTool(ToolConfig(extra={"backend": "codex"}))

        result = await tool._execute({"query": "anything"})

        assert result.error == "rate limited"
        assert result.output == ""

    def test_untrusted_annotation_url_is_not_exposed(self):
        response = _normalize_deepseek_response(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "answer",
                                "annotations": [
                                    {"url": "javascript:alert(1)", "title": "bad"}
                                ],
                            }
                        ],
                    }
                ]
            },
            "q",
            10,
            "deepseek-v4-flash",
        )

        assert response.sources == []
        assert "javascript:" not in response.output

    def test_provider_text_links_remain_visible_but_are_not_verified(self):
        response = _normalize_deepseek_response(
            {"output_text": "Read https://example.com/provider-text"},
            "q",
            10,
            "deepseek-v4-flash",
        )

        assert "https://example.com/provider-text" in response.output
        assert "unverified provider text" in response.output
        assert response.sources == []
        assert response.metadata["citation_status"] == "unverified_text"

    def test_non_citation_annotation_with_http_url_is_not_trusted(self):
        response = _normalize_deepseek_response(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "answer",
                                "annotations": [
                                    {
                                        "type": "file_reference",
                                        "url": "https://example.com/not-a-citation",
                                        "title": "not a citation",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            "q",
            10,
            "deepseek-v4-flash",
        )

        assert response.sources == []
        assert "not-a-citation" not in response.output
        assert response.metadata["annotation_count"] == 1
