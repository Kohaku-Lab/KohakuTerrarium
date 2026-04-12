"""
Web fetch tool: read a web page and return LLM-friendly content.

Tiered fallback (uses best available):
  1. Crawl4AI (pip install crawl4ai): JS rendering, anti-bot, best quality
  2. trafilatura (pip install trafilatura): good extraction, no JS
  3. Jina Reader (r.jina.ai): zero deps, JS server-side, may fail
  4. Naive httpx + html2text: always works, lowest quality
"""

from typing import Any

import html2text
import httpx

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import DEFAULT_LOG_DIR, get_logger

logger = get_logger(__name__)

MAX_CONTENT_SIZE = 100_000  # 100k chars max returned to model
FETCH_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; KohakuTerrarium/1.0)"

# Detect available backends at import time
_HAS_CRAWL4AI = False
_HAS_TRAFILATURA = False

try:
    import crawl4ai  # noqa: F401

    _HAS_CRAWL4AI = True
except ImportError:
    pass

try:
    import trafilatura  # noqa: F401

    _HAS_TRAFILATURA = True
except ImportError:
    pass


@register_builtin("web_fetch")
class WebFetchTool(BaseTool):
    """Fetch a web page and return clean, readable content.

    Automatically uses the best available backend:
    crawl4ai > trafilatura > jina reader > naive httpx.
    """

    @property
    def tool_name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Read a web page and return its content in clean markdown format"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        url = args.get("url", "")
        if not url:
            return ToolResult(
                error="No URL provided. Usage: web_fetch(url='https://...')"
            )

        # Normalize URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # Try backends in order
        for backend_name, backend_fn in [
            ("crawl4ai", _fetch_crawl4ai),
            ("trafilatura", _fetch_trafilatura),
            ("jina", _fetch_jina),
            ("httpx", _fetch_naive),
        ]:
            try:
                content = await backend_fn(url)
                if content and content.strip():
                    # Truncate if too long
                    if len(content) > MAX_CONTENT_SIZE:
                        content = (
                            content[:MAX_CONTENT_SIZE]
                            + f"\n\n... (truncated, {len(content)} chars total)"
                        )
                    logger.info(
                        "Web fetch success",
                        url=url[:80],
                        backend=backend_name,
                        content_len=len(content),
                    )
                    return ToolResult(output=content, exit_code=0)
            except _SkipBackend:
                continue
            except Exception as e:
                logger.debug(
                    "Web fetch backend failed, trying next",
                    backend=backend_name,
                    url=url[:80],
                    error=str(e),
                )
                continue

        return ToolResult(error=f"Failed to fetch {url}. All backends failed.")


class _SkipBackend(Exception):
    """Raised when a backend is not available."""


# ── Backend implementations ────────────────────────────────────


async def _fetch_crawl4ai(url: str) -> str:
    """Fetch with Crawl4AI browser + trafilatura extraction.

    Crawl4AI renders JS pages, then trafilatura extracts clean content.
    If trafilatura is not installed, falls back to crawl4ai's raw markdown.
    """
    if not _HAS_CRAWL4AI:
        raise _SkipBackend

    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    from crawl4ai.async_logger import AsyncFileLogger

    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(verbose=False, log_console=False)
    crawl4ai_logger = AsyncFileLogger(str(DEFAULT_LOG_DIR / "crawl4ai.log"))

    async with AsyncWebCrawler(config=browser_cfg, logger=crawl4ai_logger) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise _SkipBackend

        # Best path: use trafilatura to extract content from rendered HTML
        if _HAS_TRAFILATURA and result.html:
            import trafilatura

            content = trafilatura.extract(
                result.html,
                output_format="markdown",
                include_links=True,
                include_images=False,
                include_tables=True,
            )
            if content and content.strip():
                return content

        # Fallback: crawl4ai's own markdown (includes page chrome)
        md = result.markdown
        text = str(md) if md else ""
        if not text.strip():
            raise _SkipBackend
        return text


async def _fetch_trafilatura(url: str) -> str:
    """Fetch with trafilatura (content extraction, no JS)."""
    if not _HAS_TRAFILATURA:
        raise _SkipBackend

    import trafilatura

    # Fetch HTML with httpx (async), then extract with trafilatura (sync)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    # Extract content as markdown
    content = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_images=False,
        include_tables=True,
    )
    if not content:
        raise _SkipBackend
    return content


async def _fetch_jina(url: str) -> str:
    """Fetch via Jina Reader API (zero deps, JS server-side)."""
    jina_url = f"https://r.jina.ai/{url}"
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/markdown",
        },
    ) as client:
        resp = await client.get(jina_url)
        resp.raise_for_status()
        content = resp.text

    if not content or len(content.strip()) < 50:
        raise _SkipBackend
    return content


async def _fetch_naive(url: str) -> str:
    """Naive fetch: httpx + basic HTML stripping."""
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0  # no wrapping
    return h.handle(html)
