"""
Web search tool: search the web and return structured results.

Primary backend: ``ddgs`` (DuckDuckGo Search, no API key needed).
``ddgs`` transitively requires ``primp`` — a Rust+reqwest HTTP
client wrapped via PyO3.  On Android the Rust runtime's tokio /
rustls stack hard-crashes the host process at first call, so the
Android build drops both ``ddgs`` and ``primp`` from
``requirements.txt`` (see ``packaging/android/postcreate.py``'s
``_ANDROID_DROP_PACKAGES``).

Fallback backend: ``_search_httpx_ddg`` — pure-Python scrape of
https://html.duckduckgo.com/html/ via :mod:`httpx` (already a
core dep).  Same data source as ddgs, no native code, works
everywhere httpx works.  Brittle if DuckDuckGo restyles the HTML,
but it's the same surface ddgs itself parses.

Resolution: try ddgs first; on ``ImportError`` (Android) or any
runtime failure (transient primp crash, ddgs API changes, …)
fall through to the httpx scraper.  If the scraper also fails,
return a clear tool-unavailable error instead of crashing the
agent.
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


# Optional-dep imports at module scope so the in-function-import
# lint stays clean.  ``DDGS`` resolves to the first available class
# across ``ddgs`` (newer, primp-backed) and the legacy
# ``duckduckgo_search`` (deprecated, no primp).  ``None`` when
# neither is installed — Android default after we drop both via
# ``packaging/android/postcreate.py:_ANDROID_DROP_PACKAGES``.
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
    """Search the web and return structured results.

    Uses DuckDuckGo (no API key required).
    Install: pip install ddgs  (desktop)
    On Android the primp-free httpx fallback ships by default.
    """

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
            except Exception as e:  # noqa: BLE001 — fall through to httpx
                ddgs_error = e
                logger.warning(
                    "ddgs search failed, falling back to httpx scraper",
                    error=str(e),
                )

        if not results:
            try:
                results = await _search_httpx_ddg(query, max_results, region)
            except Exception as e:
                # Both backends down — return whichever error is more
                # actionable.  The ddgs error usually carries more
                # context (e.g. rate-limit, region rejected), the
                # httpx error tends to be generic httpx.
                detail = ddgs_error or e
                return ToolResult(error=f"Search failed: {detail}")

        if not results:
            return ToolResult(output="No results found.", exit_code=0)

        # Format results for LLM
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
    """Run DuckDuckGo search via ``ddgs`` (sync library, run in executor)."""

    def _do_search():
        kwargs: dict[str, Any] = {"max_results": max_results}
        if region:
            kwargs["region"] = region
        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_search)


# Each DDG HTML result lives inside ``<div class="result">``; the
# title + URL live in ``<a class="result__a">…</a>`` and the snippet
# in a sibling ``<a class="result__snippet">…</a>``.  These regexes
# pull both per-result so we can zip them.  Robust against minor
# HTML tweaks (extra attributes, whitespace) but obviously not
# against a full DDG rebrand.
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
    """DDG sometimes wraps result URLs in its own redirector.

    The shape is ``//duckduckgo.com/l/?uddg=<url-encoded-target>&…``.
    Pull the real target out so the agent gets a clickable URL,
    not a tracker.  Non-redirect URLs pass through unchanged.
    """
    m = _RE_DDG_REDIRECT.match(href)
    if m:
        return unquote(m.group(1))
    return href


def _parse_ddg_html(body: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo HTML response into the same shape ddgs returns.

    Returns a list of ``{href, title, body}`` dicts so the formatter
    in ``_execute`` doesn't have to fork on backend.  Caps at
    ``max_results`` results.
    """
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
    """Pure-Python DDG search via the HTML endpoint.

    Used as the Android-default and as a fallback when ``ddgs``
    fails on desktop.  Hits the same HTML interface ddgs itself
    scrapes, but via ``httpx`` (already a core dep) so there's no
    native primp/Rust footprint.

    Caps at 30 results regardless of ``max_results`` — the HTML
    endpoint returns ~30 per page and we don't paginate to keep
    the failure surface tiny.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        # DDG's HTML endpoint serves a CAPTCHA challenge to obvious
        # bot user-agents; a plain desktop-Chrome UA passes through.
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
