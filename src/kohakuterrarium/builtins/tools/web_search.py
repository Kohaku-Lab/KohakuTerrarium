"""Web search with selectable DuckDuckGo and DeepSeek backends."""

import asyncio
import html as _html
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.llm.api_keys import get_api_key, has_api_key
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_RESULTS = 10
DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


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


class SearchBackendError(RuntimeError):
    """Base error for a configured search backend."""


class SearchBackendUnavailable(SearchBackendError):
    """Raised for configuration or authentication failures."""


class SearchBackendOperationalError(SearchBackendError):
    """Raised for transient failures eligible for explicit fallback."""


@dataclass
class SearchResponse:
    """Normalized backend response rendered by ``WebSearchTool``."""

    output: str
    sources: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class DuckDuckGoSearchBackend:
    """Run the existing DDGS-to-HTML fallback chain."""

    async def search(self, query: str, max_results: int, region: str) -> SearchResponse:
        results: list[dict] = []
        ddgs_error: Exception | None = None
        if _has_ddg():
            try:
                results = await _search_ddg(query, max_results, region)
            except Exception as exc:  # noqa: BLE001 - selects the HTTP fallback
                ddgs_error = exc
                logger.warning(
                    "ddgs search failed, falling back to httpx scraper",
                    error=str(exc),
                )

        if not results:
            try:
                results = await _search_httpx_ddg(query, max_results, region)
            except Exception as exc:
                detail = ddgs_error or exc
                raise SearchBackendOperationalError(
                    f"DuckDuckGo search failed: {detail}"
                ) from exc

        if not results:
            return SearchResponse(
                output="No results found.", metadata={"backend": "duckduckgo"}
            )
        return SearchResponse(
            output=_render_ddg_results(query, results),
            sources=[
                {
                    "title": str(result.get("title", "")),
                    "url": str(result.get("href", result.get("url", ""))),
                }
                for result in results
                if result.get("href") or result.get("url")
            ],
            metadata={"backend": "duckduckgo", "result_count": len(results)},
        )


class DeepSeekSearchBackend:
    """Use DeepSeek Responses API server-side web search."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def search(self, query: str, max_results: int, region: str) -> SearchResponse:
        key = get_api_key("deepseek")
        if not key:
            raise SearchBackendUnavailable(
                "DeepSeek API key is not configured. Run: kt config key set deepseek"
            )
        preference = f" Prefer results relevant to region {region}." if region else ""
        prompt = (
            f"Search the web for: {query}.{preference} "
            f"Return a concise answer grounded in up to {max_results} sources."
        )
        payload = {
            "model": self.model,
            "input": prompt,
            "tools": [{"type": "web_search"}],
            "tool_choice": {"type": "web_search"},
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    DEEPSEEK_RESPONSES_URL, headers=headers, json=payload
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SearchBackendOperationalError(
                "DeepSeek search is temporarily unavailable"
            ) from exc

        if response.status_code in {408, 429} or response.status_code >= 500:
            raise SearchBackendOperationalError(
                f"DeepSeek search failed with HTTP {response.status_code}"
            )
        if response.status_code in {401, 403}:
            raise SearchBackendUnavailable(
                "DeepSeek authentication failed. Update the key with: "
                "kt config key set deepseek"
            )
        if response.status_code >= 400:
            raise SearchBackendUnavailable(
                f"DeepSeek search request failed with HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchBackendOperationalError(
                "DeepSeek search returned an invalid response"
            ) from exc
        if not isinstance(data, dict):
            raise SearchBackendOperationalError(
                "DeepSeek search returned an invalid response"
            )
        _raise_for_deepseek_response_status(data)
        normalized = _normalize_deepseek_response(data, query, max_results, self.model)
        if data.get("status") == "incomplete":
            reason = _deepseek_incomplete_reason(data)
            if not _deepseek_has_partial_output(data, normalized):
                raise SearchBackendUnavailable(
                    f"DeepSeek search response was incomplete ({reason})"
                )
            normalized.output = (
                f"Incomplete DeepSeek response: {reason}\n\n{normalized.output}"
            )
            normalized.metadata["response_status"] = "incomplete"
            normalized.metadata["incomplete_reason"] = reason
        elif isinstance(data.get("status"), str):
            normalized.metadata["response_status"] = data["status"]
        return normalized


@register_builtin("web_search")
class WebSearchTool(BaseTool):
    """Search using a configured backend without changing the caller's LLM."""

    def __init__(self, config=None):
        super().__init__(config=config)
        self.backend = "duckduckgo"
        self.deepseek_model = DEFAULT_DEEPSEEK_MODEL
        self.fallback = "none"
        self.refresh_runtime_options(dict(self.config.extra))

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return results with titles, URLs, and snippets"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        disabled: dict[str, str] = {}
        if not has_api_key("deepseek"):
            disabled["deepseek"] = (
                "DeepSeek API key is not configured. Run: " "kt config key set deepseek"
            )
        return {
            "backend": {
                "type": "enum",
                "values": ["duckduckgo", "deepseek"],
                "default": "duckduckgo",
                "disabled_values": disabled,
                "doc": "Search implementation used by this creature.",
            },
            "deepseek_model": {
                "type": "enum",
                "values": list(DEEPSEEK_MODELS),
                "default": DEFAULT_DEEPSEEK_MODEL,
                "doc": "DeepSeek Responses model used for web search.",
            },
            "fallback": {
                "type": "enum",
                "values": ["none", "duckduckgo"],
                "default": "none",
                "doc": "Fallback for transient DeepSeek failures only.",
            },
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.backend = str(options.get("backend", "duckduckgo"))
        self.deepseek_model = str(options.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL))
        self.fallback = str(options.get("fallback", "none"))

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(error="No query provided. Usage: web_search(query='...')")

        max_results = int(args.get("max_results", MAX_RESULTS))
        region = args.get("region", "")

        started_at = time.monotonic()
        try:
            search_backend = self._backend()
            response = await search_backend.search(query, max_results, region)
        except SearchBackendOperationalError as exc:
            if self.backend == "deepseek" and self.fallback == "duckduckgo":
                logger.warning("DeepSeek search failed, using configured fallback")
                try:
                    response = await DuckDuckGoSearchBackend().search(
                        query, max_results, region
                    )
                except SearchBackendError as fallback_exc:
                    return ToolResult(error=str(fallback_exc))
                response.metadata["fallback_from"] = "deepseek"
                response.metadata["fallback_reason"] = str(exc)
            else:
                return ToolResult(error=str(exc))
        except SearchBackendError as exc:
            return ToolResult(error=str(exc))

        response.metadata["duration_ms"] = round(
            (time.monotonic() - started_at) * 1000.0, 1
        )
        response.metadata["source_count"] = len(response.sources)
        response.metadata["session_metadata"] = _search_session_metadata(
            response.metadata
        )
        logger.info(
            "Web search complete",
            query=query[:50],
            backend=response.metadata.get("backend", self.backend),
        )
        return ToolResult(
            output=response.output,
            exit_code=0,
            metadata=response.metadata,
        )

    def _backend(self):
        if self.backend == "duckduckgo":
            return DuckDuckGoSearchBackend()
        if self.backend == "deepseek":
            return DeepSeekSearchBackend(self.deepseek_model)
        raise SearchBackendUnavailable(f"Unknown web search backend: {self.backend!r}")


def _render_ddg_results(query: str, results: list[dict]) -> str:
    lines = [f"Search results for: {query}\n"]
    for i, result in enumerate(results, 1):
        title = result.get("title", "")
        url = result.get("href", result.get("url", ""))
        snippet = result.get("body", result.get("snippet", ""))
        lines.append(f"## {i}. {title}")
        lines.append(f"URL: {url}")
        if snippet:
            lines.append(snippet)
        lines.append("")
    return "\n".join(lines)


def _normalize_deepseek_response(
    data: dict[str, Any], query: str, max_results: int, model: str
) -> SearchResponse:
    texts: list[str] = []
    sources: list[dict[str, str]] = []
    actions: list[dict[str, Any]] = []
    annotation_count = 0
    if isinstance(data.get("output_text"), str):
        texts.append(data["output_text"])
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action")
            if isinstance(action, dict):
                actions.append(
                    {
                        key: action[key]
                        for key in ("type", "query", "url", "pattern")
                        if key in action
                    }
                )
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            raw_annotations = part.get("annotations")
            annotations = raw_annotations if isinstance(raw_annotations, list) else []
            annotation_count += len(annotations)
            for annotation in annotations:
                source = _source_from_annotation(annotation)
                if source and source not in sources:
                    sources.append(source)
    sources = sources[:max_results]
    body = "\n\n".join(text.strip() for text in texts if text.strip())
    if not body and not sources:
        body = "No results found."
    output = f"Search results for: {query}\n\n{body}"
    has_text_links = "http://" in body or "https://" in body
    citation_status = (
        "verified" if sources else "unverified_text" if has_text_links else "none"
    )
    if has_text_links:
        if sources:
            output += (
                "\n\nCitation note: Only links in the Sources list below were "
                "returned as structured citations; other links in the answer text "
                "are unverified provider text."
            )
        else:
            output += (
                "\n\nCitation note: Links in the answer text are unverified provider "
                "text because no structured citation annotations were returned."
            )
    if sources:
        output += "\n\nSources:\n" + "\n".join(
            f"{index}. [{source['title']}]({source['url']})"
            for index, source in enumerate(sources, 1)
        )
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return SearchResponse(
        output=output,
        sources=sources,
        metadata={
            "backend": "deepseek",
            "model": model,
            "sources": sources,
            "verified_sources": sources,
            "source_count": len(sources),
            "annotation_count": annotation_count,
            "citation_status": citation_status,
            "actions": actions,
            "usage": usage,
            "response_status": (
                data["status"]
                if isinstance(data.get("status"), str)
                else "not_reported"
            ),
        },
    )


def _search_session_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Select non-secret search diagnostics for session persistence."""
    keys = (
        "backend",
        "model",
        "response_status",
        "citation_status",
        "annotation_count",
        "source_count",
        "result_count",
        "fallback_from",
        "fallback_reason",
        "duration_ms",
    )
    result = {key: metadata[key] for key in keys if key in metadata}
    sources = metadata.get("verified_sources")
    if isinstance(sources, list):
        result["verified_sources"] = [
            {"title": source["title"], "url": source["url"]}
            for source in sources
            if isinstance(source, dict)
            and isinstance(source.get("title"), str)
            and isinstance(source.get("url"), str)
        ]
    usage = metadata.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {
            str(key): value
            for key, value in usage.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    return result


_TRANSIENT_DEEPSEEK_ERROR_MARKERS = (
    "rate_limit",
    "server_error",
    "internal_error",
    "overload",
    "service_unavailable",
    "temporarily_unavailable",
    "timeout",
)


def _raise_for_deepseek_response_status(data: dict[str, Any]) -> None:
    """Map terminal Responses API state into the configured fallback boundary."""
    status = data.get("status")
    if status in (None, "completed", "incomplete"):
        return
    if status == "failed":
        code = _deepseek_error_code(data.get("error"))
        message = f"DeepSeek search failed ({code})"
        if any(marker in code.lower() for marker in _TRANSIENT_DEEPSEEK_ERROR_MARKERS):
            raise SearchBackendOperationalError(message)
        raise SearchBackendUnavailable(message)
    if isinstance(status, str):
        raise SearchBackendUnavailable(
            f"DeepSeek search returned unexpected status {status!r}"
        )
    raise SearchBackendUnavailable("DeepSeek search returned an invalid status")


def _deepseek_error_code(error: Any) -> str:
    if isinstance(error, dict):
        raw = error.get("code") or error.get("type")
    else:
        raw = None
    code = str(raw or "unknown_error")[:80]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", code)


def _deepseek_incomplete_reason(data: dict[str, Any]) -> str:
    details = data.get("incomplete_details")
    raw = details.get("reason") if isinstance(details, dict) else None
    reason = str(raw or "unknown_reason")[:80]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", reason)


def _deepseek_has_partial_output(
    data: dict[str, Any], response: SearchResponse
) -> bool:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return True
    if response.sources:
        return True
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
                and part["text"].strip()
            ):
                return True
    return False


def _source_from_annotation(annotation: Any) -> dict[str, str] | None:
    if not isinstance(annotation, dict):
        return None
    citation = annotation.get("url_citation")
    if isinstance(citation, dict):
        source = citation
    elif annotation.get("type") == "url_citation":
        source = annotation
    else:
        return None
    url = str(source.get("url", ""))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    title = str(source.get("title") or parsed.netloc)
    return {"title": title, "url": url}


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
