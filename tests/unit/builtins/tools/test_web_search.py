"""Unit tests for the web_search tool's parser + fallback path.

The real DDG endpoint is NOT hit — these tests pin the HTML parser
against fixed snippets and the backend-selection logic against
monkey-patched search functions.  Live-network behaviour is the
integration tier's concern.
"""

import pytest

from kohakuterrarium.builtins.tools import web_search
from kohakuterrarium.builtins.tools.web_search import (
    WebSearchTool,
    _parse_ddg_html,
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
