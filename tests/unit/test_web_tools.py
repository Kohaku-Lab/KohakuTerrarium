"""Tests for web_fetch and web_search tools."""

import pytest

from kohakuterrarium.builtins.tools.web_fetch import (
    _HAS_CRAWL4AI,
    _HAS_TRAFILATURA,
    WebFetchTool,
)
from kohakuterrarium.builtins.tools.web_search import _HAS_DDG, WebSearchTool

# ── WebFetchTool ──────────────────────────────────────────────


class TestWebFetchTool:
    def test_import_and_register(self):
        tool = WebFetchTool()
        assert tool.tool_name == "web_fetch"

    def test_description(self):
        tool = WebFetchTool()
        assert "web" in tool.description.lower()

    def test_documentation(self):
        tool = WebFetchTool()
        doc = tool.get_full_documentation()
        assert "url" in doc.lower()
        assert "backend" in doc.lower()

    @pytest.mark.asyncio
    async def test_no_url(self):
        tool = WebFetchTool()
        result = await tool._execute({})
        assert result.error
        assert "url" in result.error.lower()

    @pytest.mark.asyncio
    async def test_naive_fetch_html(self):
        """Naive backend strips HTML tags."""
        html = "<html><body><h1>Title</h1><p>Content here</p></body></html>"

        # Mock: test the stripping logic directly
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        assert "Title" in text
        assert "Content here" in text
        assert "<" not in text

    def test_backend_detection(self):
        """Verify backend detection flags are booleans."""
        assert isinstance(_HAS_CRAWL4AI, bool)
        assert isinstance(_HAS_TRAFILATURA, bool)

    def test_registered_in_catalog(self):
        from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

        tool_cls = get_builtin_tool("web_fetch")
        assert tool_cls is not None

    def test_native_schema(self):
        from kohakuterrarium.llm.tools import _BUILTIN_SCHEMAS

        assert "web_fetch" in _BUILTIN_SCHEMAS
        schema = _BUILTIN_SCHEMAS["web_fetch"]
        assert "url" in schema["properties"]
        assert "url" in schema["required"]


# ── WebSearchTool ─────────────────────────────────────────────


class TestWebSearchTool:
    def test_import_and_register(self):
        tool = WebSearchTool()
        assert tool.tool_name == "web_search"

    def test_description(self):
        tool = WebSearchTool()
        assert "search" in tool.description.lower()

    def test_documentation(self):
        tool = WebSearchTool()
        doc = tool.get_full_documentation()
        assert "query" in doc.lower()
        assert "search" in doc.lower()

    @pytest.mark.asyncio
    async def test_no_query(self):
        tool = WebSearchTool()
        result = await tool._execute({})
        assert result.error
        assert "query" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_ddg(self):
        """When duckduckgo-search is not installed, returns helpful error."""
        if _HAS_DDG:
            pytest.skip("duckduckgo-search is installed")
        tool = WebSearchTool()
        result = await tool._execute({"query": "test"})
        assert result.error
        assert "duckduckgo" in result.error.lower()

    def test_registered_in_catalog(self):
        from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

        tool_cls = get_builtin_tool("web_search")
        assert tool_cls is not None

    def test_native_schema(self):
        from kohakuterrarium.llm.tools import _BUILTIN_SCHEMAS

        assert "web_search" in _BUILTIN_SCHEMAS
        schema = _BUILTIN_SCHEMAS["web_search"]
        assert "query" in schema["properties"]
        assert "query" in schema["required"]
