"""DuckDuckGo web search with an HTTP fallback for unsupported runtimes.

The optional ``ddgs`` backend is preferred where available. A pure-HTTP parser
keeps search usable on platforms where its native dependency cannot run.
"""

import asyncio
import html as _html
import re
from typing import Any
from urllib.parse import unquote

import httpx

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_RESULTS = 10


# Prefer the current optional package, then its legacy predecessor; ``None``
# selects the HTTP backend on installations without either dependency.
DDGS: Any = None
try:
    from ddgs import DDGS  # type: ignore[no-redef]
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    except ImportError:
        pass


def _has_ddg() -> bool:
    return DDGS is not None


@register_builtin("web_search")
class WebSearchTool(BaseTool):
    """Return DuckDuckGo result titles, URLs, and snippets."""

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return results with titles, URLs, and snippets"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(error="No query provided. Usage: web_search(query='...')")

        max_results = int(args.get("max_results", MAX_RESULTS))
        region = args.get("region", "")

        results: list[dict] = []
        ddgs_error: Exception | None = None
        if _has_ddg():
            try:
                results = await _search_ddg(query, max_results, region)
            except Exception as e:  # noqa: BLE001 - backend failure selects fallback
                ddgs_error = e
                logger.warning(
                    "ddgs search failed, falling back to httpx scraper",
                    error=str(e),
                )

        if not results:
            try:
                results = await _search_httpx_ddg(query, max_results, region)
            except Exception as e:
                # The primary backend generally carries more actionable context
                # than the lower-level HTTP failure.
                detail = ddgs_error or e
                return ToolResult(error=f"Search failed: {detail}")

        if not results:
            return ToolResult(output="No results found.", exit_code=0)

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("href", r.get("url", ""))
            snippet = r.get("body", r.get("snippet", ""))
            lines.append(f"## {i}. {title}")
            lines.append(f"URL: {url}")
            if snippet:
                lines.append(snippet)
            lines.append("")

        logger.info("Web search complete", query=query[:50], results=len(results))
        return ToolResult(output="\n".join(lines), exit_code=0)


async def _search_ddg(query: str, max_results: int, region: str) -> list[dict]:
    """Run the synchronous ``ddgs`` client without blocking the event loop."""

    def _do_search():
        kwargs: dict[str, Any] = {"max_results": max_results}
        if region:
            kwargs["region"] = region
        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_search)


# Title and snippet patterns intentionally tolerate extra attributes and
# whitespace but remain coupled to DuckDuckGo's HTML result class names.
_RE_DDG_TITLE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_RE_DDG_SNIPPET = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_RE_DDG_HTML_TAG = re.compile(r"<[^>]+>")
_RE_DDG_REDIRECT = re.compile(r"^//duckduckgo\.com/l/\?(?:.*&)?uddg=([^&]+)")


def _strip_html(text: str) -> str:
    return _html.unescape(_RE_DDG_HTML_TAG.sub("", text)).strip()


def _unwrap_ddg_redirect(href: str) -> str:
    """Extract the target from DuckDuckGo redirect URLs when present."""
    m = _RE_DDG_REDIRECT.match(href)
    if m:
        return unquote(m.group(1))
    return href


def _parse_ddg_html(body: str, max_results: int) -> list[dict]:
    """Parse HTML results into the same mapping shape as ``ddgs``."""
    results: list[dict] = []
    for m in _RE_DDG_TITLE.finditer(body):
        href = _unwrap_ddg_redirect(_html.unescape(m.group(1)))
        title = _strip_html(m.group(2))
        results.append({"href": href, "title": title, "body": ""})
        if len(results) >= max_results:
            break
    snippets = [_strip_html(m.group(1)) for m in _RE_DDG_SNIPPET.finditer(body)]
    for i, snip in enumerate(snippets[: len(results)]):
        results[i]["body"] = snip
    return results


async def _search_httpx_ddg(
    query: str,
    max_results: int,
    region: str,
) -> list[dict]:
    """Search DuckDuckGo's HTML endpoint without native dependencies."""
    url = "https://html.duckduckgo.com/html/"
    headers = {
        # The HTML endpoint challenges obvious bot user agents.
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    payload: dict[str, str] = {"q": query, "b": "", "df": ""}
    if region:
        payload["kl"] = region

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=15.0
    ) as client:
        resp = await client.post(url, data=payload)
        resp.raise_for_status()
        return _parse_ddg_html(resp.text, max_results)
