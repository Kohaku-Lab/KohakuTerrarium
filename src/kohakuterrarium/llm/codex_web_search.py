"""Standalone Codex-subscription web search for non-Codex controllers.

This module deliberately treats search as an account-backed KT capability,
not as a provider-native tool of the creature's active LLM.  Any controller
can therefore call the ordinary ``web_search`` function while this helper
uses cached ``kt login codex`` credentials for the hosted search request.
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx

from kohakuterrarium.llm.codex_auth import CodexTokens, refresh_tokens
from kohakuterrarium.llm.codex_provider import CODEX_BASE_URL

try:
    from openai import AsyncOpenAI

    HAS_OPENAI = True
except ImportError:  # pragma: no cover - optional dependency boundary
    AsyncOpenAI = None  # type: ignore[misc, assignment]
    HAS_OPENAI = False


DEFAULT_CODEX_SEARCH_MODEL = "gpt-5.6-luna"
CODEX_SEARCH_MODELS = (
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)
_TOKEN_REFRESH_LOCK = asyncio.Lock()
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "msclkid"})
_TEXT_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]{}]+")


class CodexSearchError(RuntimeError):
    """Base error for the account-backed Codex search client."""


class CodexSearchUnavailable(CodexSearchError):
    """The subscription credential or requested capability is unavailable."""


class CodexSearchOperationalError(CodexSearchError):
    """A transient remote failure made this search attempt unusable."""


@dataclass
class CodexSearchResult:
    """Normalized result returned to the ordinary KT web-search tool."""

    output: str
    sources: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def codex_search_available() -> bool:
    """Return whether a cached Codex subscription credential can be loaded."""
    return HAS_OPENAI and CodexTokens.load() is not None


class CodexSubscriptionSearchBackend:
    """Run one hosted web-search request using cached Codex OAuth credentials."""

    def __init__(self, model: str = DEFAULT_CODEX_SEARCH_MODEL) -> None:
        self.model = model

    async def search(
        self, query: str, max_results: int, region: str
    ) -> CodexSearchResult:
        tokens = await _load_valid_tokens()
        prompt = _search_prompt(query, max_results, region)
        cache_key = hashlib.sha256(
            f"kt-codex-search:{self.model}".encode()
        ).hexdigest()[:32]
        client = AsyncOpenAI(
            api_key=tokens.access_token,
            base_url=CODEX_BASE_URL,
            timeout=60.0,
            max_retries=1,
            http_client=httpx.AsyncClient(timeout=60.0),
        )
        collector = _SearchCollector(query=query, model=self.model)
        try:
            stream = await client.responses.create(
                model=self.model,
                instructions=(
                    "Use web search to answer the request. Prefer primary sources, "
                    "keep the answer concise, and preserve source citations."
                ),
                input=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                tools=[{"type": "web_search", "external_web_access": True}],
                tool_choice={"type": "web_search"},
                include=["web_search_call.action.sources"],
                store=False,
                stream=True,
                prompt_cache_key=cache_key,
                extra_headers={"session_id": cache_key},
            )
            async for event in stream:
                collector.observe(event)
        except CodexSearchError:
            raise
        except Exception as exc:
            raise _map_request_error(exc) from exc
        finally:
            await client.close()
        return collector.result(max_results)


async def _load_valid_tokens() -> CodexTokens:
    if not HAS_OPENAI:
        raise CodexSearchUnavailable(
            "Codex search requires the OpenAI SDK; install the standard KT dependencies"
        )
    tokens = CodexTokens.load()
    if tokens is None:
        raise CodexSearchUnavailable(
            "Codex subscription is not connected. Run: kt login codex"
        )
    if tokens.is_expired():
        async with _TOKEN_REFRESH_LOCK:
            tokens = CodexTokens.load()
            if tokens is None:
                raise CodexSearchUnavailable(
                    "Codex subscription is not connected. Run: kt login codex"
                )
            if tokens.is_expired():
                try:
                    tokens = await refresh_tokens(tokens)
                except Exception as exc:
                    recovered = CodexTokens.load()
                    if recovered is not None and not recovered.is_expired():
                        return recovered
                    raise CodexSearchUnavailable(
                        "Codex subscription login expired. Run: kt login codex"
                    ) from exc
    return tokens


def _search_prompt(query: str, max_results: int, region: str) -> str:
    region_hint = f" Prefer sources relevant to region {region}." if region else ""
    return (
        f"Search the live web for: {query}.{region_hint} "
        f"Answer using no more than {max_results} distinct sources."
    )


def _map_request_error(exc: Exception) -> CodexSearchError:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    status = status or getattr(response, "status_code", None)
    if status in {401, 403}:
        return CodexSearchUnavailable(
            "Codex subscription authentication failed. Run: kt login codex"
        )
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return CodexSearchOperationalError(
            f"Codex search temporarily failed with HTTP {status}"
        )
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return CodexSearchOperationalError("Codex search is temporarily unavailable")
    if isinstance(status, int) and status >= 400:
        return CodexSearchUnavailable(
            f"Codex search request was rejected with HTTP {status}"
        )
    return CodexSearchOperationalError(f"Codex search failed: {exc}")


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _source(value: Any) -> dict[str, str] | None:
    citation = _value(value, "url_citation")
    raw = citation if citation is not None else value
    url = str(_value(raw, "url", "") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    title = str(_value(raw, "title", "") or parsed.netloc)
    return {"title": title, "url": url}


def _citation_source(value: Any) -> dict[str, str] | None:
    """Accept only Responses URL-citation annotations as cited sources."""
    nested = _value(value, "url_citation")
    if nested is not None:
        return _source(nested)
    if _value(value, "type") != "url_citation":
        return None
    return _source(value)


def _action(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("type", "query", "queries", "url", "pattern"):
        item = _value(value, key)
        if item not in (None, "", []):
            result[key] = item
    return result


def _has_source_url(sources: list[dict[str, str]], candidate: dict[str, str]) -> bool:
    candidate_key = _source_url_key(candidate["url"])
    return any(_source_url_key(source["url"]) == candidate_key for source in sources)


def _source_url_key(url: str) -> str:
    """Normalize non-content URL differences used by source tracking."""
    parsed = urlparse(url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_QUERY_KEYS
        ]
    )
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=query,
        fragment="",
    ).geturl()


def _text_url_keys(text: str) -> set[str]:
    return {
        _source_url_key(_clean_text_url(match.group(0)))
        for match in _TEXT_URL_PATTERN.finditer(text)
    }


def _clean_text_url(url: str) -> str:
    cleaned = url.rstrip(".,;:!?\"'")
    while cleaned.endswith(")") and cleaned.count(")") > cleaned.count("("):
        cleaned = cleaned[:-1]
    return cleaned


@dataclass
class _SearchCollector:
    query: str
    model: str
    text: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    citation_sources: list[dict[str, str]] = field(default_factory=list)
    action_sources: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    response_status: str = "not_reported"
    annotation_count: int = 0

    def observe(self, event: Any) -> None:
        event_type = str(_value(event, "type", "") or "")
        if event_type == "response.output_text.delta":
            delta = _value(event, "delta", "")
            if isinstance(delta, str):
                self.text.append(delta)
            return
        if event_type == "response.output_item.done":
            self._observe_item(_value(event, "item"))
            return
        if event_type == "response.completed":
            response = _value(event, "response")
            self._observe_final_search_calls(response)
            self.response_status = str(_value(response, "status", "completed"))
            self.usage = _usage(_value(response, "usage"))
            return
        if event_type in {"response.failed", "response.incomplete"}:
            response = _value(event, "response")
            self._observe_final_search_calls(response)
            self.response_status = str(
                _value(response, "status", event_type.removeprefix("response."))
            )
            error = _value(response, "error") or _value(event, "error")
            message = _value(error, "message", "") or self.response_status
            if event_type == "response.failed":
                raise CodexSearchOperationalError(f"Codex search {message}")
            if not self.text and not self.sources:
                raise CodexSearchOperationalError(f"Codex search {message}")

    def _observe_final_search_calls(self, response: Any) -> None:
        """Collect included action sources even if the stream omitted item events."""
        for item in _value(response, "output", []) or []:
            if _value(item, "type", "") == "web_search_call":
                self._observe_item(item)

    def _observe_item(self, item: Any) -> None:
        item_type = _value(item, "type", "")
        if item_type == "web_search_call":
            action = _action(_value(item, "action"))
            if action and action not in self.actions:
                self.actions.append(action)
            for raw in _value(_value(item, "action"), "sources", []) or []:
                self._add_source(raw, origin=self.action_sources)
            return
        if item_type != "message":
            return
        for part in _value(item, "content", []) or []:
            for annotation in _value(part, "annotations", []) or []:
                self.annotation_count += 1
                normalized = _citation_source(annotation)
                if normalized is not None:
                    self._add_normalized_source(
                        normalized, origin=self.citation_sources
                    )

    def _add_source(self, raw: Any, *, origin: list[dict[str, str]]) -> None:
        normalized = _source(raw)
        if normalized is None:
            return
        self._add_normalized_source(normalized, origin=origin)

    def _add_normalized_source(
        self, normalized: dict[str, str], *, origin: list[dict[str, str]]
    ) -> None:
        if not _has_source_url(origin, normalized):
            origin.append(normalized)
        if not _has_source_url(self.sources, normalized):
            self.sources.append(normalized)

    def result(self, max_results: int) -> CodexSearchResult:
        # The search-call item normally arrives before the cited message. Put
        # inline citations first so a source limit cannot hide the answer's
        # most relevant references behind the larger consulted-source list.
        ordered_sources: list[dict[str, str]] = []
        for source in [*self.citation_sources, *self.action_sources]:
            if not _has_source_url(ordered_sources, source):
                ordered_sources.append(source)
        sources = ordered_sources[:max_results]
        selected_url_keys = {_source_url_key(source["url"]) for source in sources}
        citation_sources = [
            source
            for source in self.citation_sources
            if _source_url_key(source["url"]) in selected_url_keys
        ]
        action_sources = [
            source
            for source in self.action_sources
            if _source_url_key(source["url"]) in selected_url_keys
        ]
        body = "".join(self.text).strip() or "No results found."
        if self.response_status == "incomplete":
            body = f"Incomplete Codex search response.\n\n{body}"
        output = f"Search results for: {self.query}\n\n{body}"
        text_url_keys = _text_url_keys(body)
        has_text_links = bool(text_url_keys)
        unverified_text_links = text_url_keys - selected_url_keys
        citation_status = (
            "verified"
            if citation_sources
            else (
                "grounded"
                if action_sources
                else "unverified_text" if has_text_links else "none"
            )
        )
        if unverified_text_links:
            if sources:
                output += (
                    "\n\nCitation note: Only links in the Sources list below were "
                    "returned as structured citations; other links in the answer "
                    "text are unverified provider text."
                )
            else:
                output += (
                    "\n\nCitation note: Links in the answer text are unverified "
                    "provider text because no structured citations were returned."
                )
        if action_sources and not citation_sources:
            output += (
                "\n\nGrounding note: The Sources list contains structured URLs "
                "reported as consulted by OpenAI web search; the answer did not "
                "include inline URL citations."
            )
        if sources:
            output += "\n\nSources:\n" + "\n".join(
                f"{index}. [{source['title']}]({source['url']})"
                for index, source in enumerate(sources, 1)
            )
        return CodexSearchResult(
            output=output,
            sources=sources,
            metadata={
                "backend": "codex",
                "model": self.model,
                "response_status": self.response_status,
                "citation_status": citation_status,
                "annotation_count": self.annotation_count,
                "source_count": len(sources),
                "verified_source_count": len(citation_sources),
                "action_source_count": len(action_sources),
                "sources": sources,
                "verified_sources": citation_sources,
                "consulted_sources": action_sources,
                "actions": self.actions,
                "usage": self.usage,
            },
        )


def _usage(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    details = _value(value, "input_tokens_details")
    return {
        "prompt_tokens": int(_value(value, "input_tokens", 0) or 0),
        "completion_tokens": int(_value(value, "output_tokens", 0) or 0),
        "total_tokens": int(_value(value, "total_tokens", 0) or 0),
        "cached_tokens": int(_value(details, "cached_tokens", 0) or 0),
    }
