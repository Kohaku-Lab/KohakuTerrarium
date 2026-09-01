"""DuckDuckGo SDK/HTML implementation for the built-in web-search tool."""

import asyncio
import html as _html
import re
from typing import Any
from urllib.parse import unquote

import httpx

DDGS: Any = None
try:
    from ddgs import DDGS  # type: ignore[no-redef]
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    except ImportError:
        pass


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
    match = _RE_DDG_REDIRECT.match(href)
    if match:
        return unquote(match.group(1))
    return href


def _parse_ddg_html(body: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    for match in _RE_DDG_TITLE.finditer(body):
        href = _unwrap_ddg_redirect(_html.unescape(match.group(1)))
        title = _strip_html(match.group(2))
        results.append({"href": href, "title": title, "body": ""})
        if len(results) >= max_results:
            break
    snippets = [_strip_html(match.group(1)) for match in _RE_DDG_SNIPPET.finditer(body)]
    for index, snippet in enumerate(snippets[: len(results)]):
        results[index]["body"] = snippet
    return results


async def _search_httpx_ddg(
    query: str,
    max_results: int,
    region: str,
) -> list[dict]:
    url = "https://html.duckduckgo.com/html/"
    headers = {
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
        response = await client.post(url, data=payload)
        response.raise_for_status()
        return _parse_ddg_html(response.text, max_results)
