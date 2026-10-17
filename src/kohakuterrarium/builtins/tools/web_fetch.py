"""Fetch web pages through progressively simpler Markdown extractors."""

import time
from typing import Any

import html2text
import httpx

from kohakuterrarium.builtins.tools.deepseek_web import (
    DeepSeekWebClient,
    DeepSeekWebError,
    DeepSeekWebTransientError,
    bounded_text,
    integer_argument,
    validate_url,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.llm.api_keys import has_api_key
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import DEFAULT_LOG_DIR, get_logger

logger = get_logger(__name__)

MAX_CONTENT_SIZE = 100_000  # Bound fetched pages before adding them to context.
FETCH_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; KohakuTerrarium/1.0)"


@register_builtin("web_fetch")
class WebFetchTool(BaseTool):
    """Return a web page as bounded Markdown using the best available backend."""

    supports_background = True

    def __init__(self, config=None):
        super().__init__(config=config)
        self.refresh_runtime_options(dict(self.config.extra))

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        disabled = (
            {}
            if has_api_key("deepseek")
            else {
                "deepseek": "DeepSeek API key is not configured. Run: kt config key set deepseek"
            }
        )
        return {
            "backend": {
                "type": "enum",
                "values": ["auto", "deepseek"],
                "default": "auto",
                "disabled_values": disabled,
                "doc": "auto uses the existing extractor chain; deepseek uses Fetch API.",
            },
            "fallback": {
                "type": "enum",
                "values": ["none", "auto"],
                "default": "none",
                "doc": "Use the extractor chain only after transient DeepSeek failures.",
            },
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.backend = str(options.get("backend", "auto"))
        self.fallback = str(options.get("fallback", "none"))

    @property
    def tool_name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch one URL and return it as markdown. Use when you have the address. Not for finding pages - use web_search."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            return ToolResult(
                error="No URL provided. Usage: web_fetch(url='https://...')"
            )
        try:
            limit = integer_argument(
                args.get("max_length", MAX_CONTENT_SIZE), "max_length", MAX_CONTENT_SIZE
            )
        except DeepSeekWebError as exc:
            return ToolResult(error=str(exc))
        if "://" not in url:
            url = "https://" + url
        started = time.monotonic()
        selected, fallback = self.backend, self.fallback
        metadata = {"requested_backend": selected, "url": url}
        try:
            validate_url(url)
            if selected == "deepseek":
                try:
                    page = await DeepSeekWebClient().fetch(url)
                    result = ToolResult(output=page["content"], exit_code=0)
                    metadata.update(backend="deepseek", title=page["title"])
                except DeepSeekWebTransientError as exc:
                    if fallback != "auto":
                        raise
                    result = await self._fetch_auto(url)
                    metadata.update(fallback_from="deepseek", fallback_reason=str(exc))
            elif selected == "auto":
                result = await self._fetch_auto(url)
            else:
                return ToolResult(error=f"Unknown web fetch backend: {selected!r}")
        except DeepSeekWebError as exc:
            return ToolResult(error=str(exc))
        if not result.success:
            return result
        metadata.update(result.metadata)
        content = result.get_text_output()
        metadata.update(
            content_chars=len(content),
            truncated=len(content) > limit,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        result.output = bounded_text(content, limit)
        result.metadata = {**metadata, "session_metadata": dict(metadata)}
        return result

    async def _fetch_auto(self, url: str) -> ToolResult:
        for backend_name, backend_fn in [
            ("crawl4ai", _fetch_crawl4ai),
            ("trafilatura", _fetch_trafilatura),
            ("jina", _fetch_jina),
            ("httpx", _fetch_naive),
        ]:
            try:
                content = await backend_fn(url)
                if content and content.strip():
                    logger.info(
                        "Web fetch success",
                        url=url[:80],
                        backend=backend_name,
                        content_len=len(content),
                    )
                    return ToolResult(
                        output=content, exit_code=0, metadata={"backend": backend_name}
                    )
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
    """Signal that resolution should continue with the next backend."""


async def _fetch_crawl4ai(url: str) -> str:
    """Render with Crawl4AI and prefer trafilatura extraction."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.async_logger import AsyncFileLogger
    except ImportError:
        raise _SkipBackend from None

    browser_cfg = BrowserConfig(headless=True, verbose=False)
    run_cfg = CrawlerRunConfig(verbose=False, log_console=False)
    crawl4ai_logger = AsyncFileLogger(str(DEFAULT_LOG_DIR / "crawl4ai.log"))

    async with AsyncWebCrawler(config=browser_cfg, logger=crawl4ai_logger) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise _SkipBackend

        if result.html:
            try:
                import trafilatura
            except ImportError:
                trafilatura = None
            if trafilatura is not None:
                content = trafilatura.extract(
                    result.html,
                    output_format="markdown",
                    include_links=True,
                    include_images=False,
                    include_tables=True,
                )
                if content and content.strip():
                    return content

        md = result.markdown
        text = str(md) if md else ""
        if not text.strip():
            raise _SkipBackend
        return text


async def _fetch_trafilatura(url: str) -> str:
    """Fetch static HTML and extract its main content with trafilatura."""
    try:
        import trafilatura
    except ImportError:
        raise _SkipBackend from None

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

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
    """Fetch server-rendered Markdown through the Jina Reader API."""
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
    """Fetch HTML directly and convert it with basic structural stripping."""
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
    h.body_width = 0  # Preserve source line structure for model readability.
    return h.handle(html)
