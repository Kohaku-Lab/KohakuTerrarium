"""Direct DeepSeek Search and Fetch requests with bounded retries and output."""

import asyncio
import ipaddress
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from kohakuterrarium.builtins.tools.web_search_types import SearchResponse
from kohakuterrarium.llm.api_keys import get_api_key

BASE_URL = "https://api.deepseek.com/v1/tools"
REQUEST_TIMEOUT = 30.0
MAX_ATTEMPTS = 3
MAX_RESULT_CHARS = 10_000
MAX_SEARCH_CHARS = 100_000
_PACE_LOCK = threading.Lock()
_NEXT_REQUEST: dict[str, float] = {}


class DeepSeekWebError(RuntimeError):
    """A terminal request, authentication, policy, or response error."""


class DeepSeekWebTransientError(DeepSeekWebError):
    """A transient failure eligible for an explicitly configured fallback."""


def integer_argument(value: Any, name: str, maximum: int | None = None) -> int:
    """Accept integers from native calls and integer strings from text calls."""
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            pass
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        bound = (
            f" between 1 and {maximum}" if maximum is not None else " greater than 0"
        )
        raise DeepSeekWebError(f"{name} must be an integer{bound}")
    return value


def boolean_argument(value: Any, name: str) -> bool:
    """Accept booleans and the text-call literals true/false."""
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        value = value.lower() == "true"
    if not isinstance(value, bool):
        raise DeepSeekWebError(f"{name} must be a boolean")
    return value


def validate_url(url: str) -> None:
    """Validate a credential-free HTTP(S) URL."""
    if not isinstance(url, str):
        raise DeepSeekWebError("URL must be a string")
    try:
        parsed = urlsplit(url)
        valid = (
            0 < len(url) <= 8192
            and parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() or ord(char) < 32 for char in url)
        )
        parsed.port
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise DeepSeekWebError(
            "URL must be HTTP(S), include a host, contain no credentials, and be at most 8192 characters"
        )


def _short_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise DeepSeekWebError(
            f"{name} must contain 1-128 Unicode characters and not be blank"
        )


def _pace_delay(endpoint: str) -> float:
    with _PACE_LOCK:
        now = time.monotonic()
        delay = _NEXT_REQUEST.get(endpoint, 0.0) - now
        if delay <= 0:
            _NEXT_REQUEST[endpoint] = now + 0.21
        return max(0.0, delay)


async def _pace(endpoint: str) -> None:
    while delay := _pace_delay(endpoint):
        await asyncio.sleep(delay)


class DeepSeekWebClient:
    """Call the model-independent Search and Fetch APIs."""

    async def search(
        self,
        query: str,
        count: int = 10,
        *,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str = "",
    ) -> dict[str, Any]:
        _short_text(query, "query")
        if type(count) is not int or not 1 <= count <= 50:
            raise DeepSeekWebError("max_results must be an integer between 1 and 50")
        if not isinstance(need_content, bool):
            raise DeepSeekWebError("need_content must be a boolean")
        payload = {"query": query, "count": count, "need_content": need_content}
        if ori_question is not None:
            _short_text(ori_question, "ori_question")
            payload["ori_question"] = ori_question
        if not isinstance(localization, str):
            raise DeepSeekWebError("localization must be an IP address")
        if localization:
            try:
                ipaddress.ip_address(localization)
            except ValueError:
                raise DeepSeekWebError(
                    "localization must be an IP address, not a region code"
                ) from None
            payload["localization"] = localization
        data = await self._request("search", payload)
        results = data.get("results")
        if not isinstance(results, list):
            raise DeepSeekWebError("DeepSeek Search returned an invalid results array")
        for result in results:
            if not isinstance(result, dict) or not all(
                isinstance(result.get(field), str)
                for field in ("title", "url", "summary", "snippet", "content")
            ):
                raise DeepSeekWebError("DeepSeek Search returned an invalid result")
            validate_url(result["url"])
        return data

    async def fetch(self, url: str) -> dict[str, str]:
        validate_url(url)
        data = await self._request("fetch", {"url": url})
        if not isinstance(data.get("title"), str) or not isinstance(
            data.get("content"), str
        ):
            raise DeepSeekWebError("DeepSeek Fetch returned an invalid page")
        if not data["content"].strip():
            raise DeepSeekWebError("DeepSeek Fetch returned no usable page content")
        return {"title": data["title"], "content": data["content"]}

    async def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = get_api_key("deepseek")
        if not key:
            raise DeepSeekWebError(
                "DeepSeek API key is not configured. Run: kt config key set deepseek"
            )
        try:
            return await asyncio.wait_for(
                self._attempts(endpoint, payload, key), REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise DeepSeekWebTransientError("DeepSeek request timed out") from None

    async def _attempts(
        self, endpoint: str, payload: dict[str, Any], key: str
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=False
        ) as client:
            for attempt in range(MAX_ATTEMPTS):
                await _pace(endpoint)
                retry_after = 0.0
                try:
                    response = await client.post(
                        f"{BASE_URL}/{endpoint}",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    error = "DeepSeek network request failed"
                else:
                    status = response.status_code
                    if status == 200:
                        try:
                            data = response.json()
                        except ValueError:
                            raise DeepSeekWebError(
                                "DeepSeek returned invalid JSON"
                            ) from None
                        if not isinstance(data, dict):
                            raise DeepSeekWebError(
                                "DeepSeek returned an invalid response"
                            )
                        return data
                    if status not in {408, 429} and not 500 <= status < 600:
                        details = {
                            400: "invalid request",
                            401: "API key missing or invalid",
                            402: "insufficient balance",
                            403: "request rejected by security policy",
                            404: "no usable page content",
                            413: "request too large",
                        }
                        raise DeepSeekWebError(
                            f"DeepSeek {endpoint} HTTP {status}: {details.get(status, 'request rejected')}"
                        )
                    error = f"DeepSeek {endpoint} failed with HTTP {status}"
                    try:
                        retry_after = max(
                            0.0,
                            min(
                                float(response.headers.get("Retry-After", "0")),
                                REQUEST_TIMEOUT,
                            ),
                        )
                    except ValueError:
                        pass
                if attempt + 1 == MAX_ATTEMPTS:
                    raise DeepSeekWebTransientError(error)
                await asyncio.sleep(max(0.5 * 2**attempt, retry_after))
        raise DeepSeekWebTransientError("DeepSeek request failed")


def bounded_text(text: str, limit: int) -> str:
    """Bound content including the truncation marker."""
    if len(text) <= limit:
        return text
    marker = "\n[truncated]"
    return text[: max(0, limit - len(marker))] + marker[:limit]


def render_search(
    data: dict[str, Any], query: str, count: int, need_content: bool
) -> SearchResponse:
    """Render search summaries and preserve source provenance without model citations."""
    results = data["results"][:count]
    sources = [{"title": item["title"], "url": item["url"]} for item in results]
    lines = [f"Search results for: {query}\n"]
    truncated = False
    details = []
    for index, item in enumerate(results, 1):
        lines.extend([f"## {index}. {item['title']}", f"URL: {item['url']}"])
        if isinstance(item.get("date"), str):
            lines.append(f"Date: {item['date']}")
        content = item["summary"].strip() or item["snippet"]
        if need_content and item["content"]:
            content += "\n\n" + item["content"]
        truncated |= len(content) > MAX_RESULT_CHARS
        lines.extend([bounded_text(content, MAX_RESULT_CHARS), ""])
        details.append(
            {
                key: item[key]
                for key in (
                    "title",
                    "url",
                    "date",
                    "site_name",
                    "authority",
                    "rerank_score",
                )
                if key in item
                and (item[key] is None or isinstance(item[key], (str, int, float)))
            }
        )
    if not results:
        lines.append("No results found.")
    output = "\n".join(lines)
    truncated |= len(output) > MAX_SEARCH_CHARS
    return SearchResponse(
        output=bounded_text(output, MAX_SEARCH_CHARS),
        sources=sources,
        metadata={
            "backend": "deepseek_api",
            "sources": sources,
            "results": details,
            "result_count": len(results),
            "citation_status": "search_results",
            "truncated": truncated,
        },
    )
