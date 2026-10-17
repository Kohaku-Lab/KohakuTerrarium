"""Web search with Codex-subscription, DuckDuckGo, and DeepSeek backends."""

import time
from typing import Any

import httpx

from kohakuterrarium.builtins.tools.deepseek_web import (
    DeepSeekWebClient,
    DeepSeekWebError,
    DeepSeekWebTransientError,
    boolean_argument,
    integer_argument,
    render_search,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.builtins.tools.web_search_deepseek_response import (
    _deepseek_has_partial_output,
    _deepseek_incomplete_reason,
    _normalize_deepseek_response as _normalize_deepseek_response,
    _raise_for_deepseek_response_status,
)
from kohakuterrarium.builtins.tools.web_search_duckduckgo import (
    DDGS,
    _parse_ddg_html as _parse_ddg_html,
    _search_ddg,
    _search_httpx_ddg,
    _unwrap_ddg_redirect as _unwrap_ddg_redirect,
)
from kohakuterrarium.builtins.tools.web_search_types import (
    SearchBackendError,
    SearchBackendOperationalError,
    SearchBackendUnavailable,
    SearchResponse,
)
from kohakuterrarium.llm.api_keys import get_api_key, has_api_key
from kohakuterrarium.llm.codex_web_search import (
    CODEX_SEARCH_MODELS,
    DEFAULT_CODEX_SEARCH_MODEL,
    CodexSearchOperationalError,
    CodexSearchUnavailable,
    CodexSubscriptionSearchBackend,
    codex_search_available,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_RESULTS = 10
DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_MODELS = ("deepseek-v4-pro", "deepseek-flash")
DEEPSEEK_MODEL_ALIASES = {"deepseek-v4-flash": "deepseek-flash"}


def _has_ddg() -> bool:
    return DDGS is not None


class DuckDuckGoSearchBackend:
    """Use the optional DDGS package, then the HTTP endpoint as fallback."""

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
        if self.model != "deepseek-v4-pro":
            raise SearchBackendUnavailable(
                "DeepSeek Flash does not execute Responses web search. "
                "Select backend=deepseek_api or deepseek_model=deepseek-v4-pro."
            )
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
        if not any(
            isinstance(item, dict) and item.get("type") == "web_search_call"
            for item in (data.get("output") or [])
        ):
            raise SearchBackendUnavailable("DeepSeek returned no web search call.")
        return normalized


@register_builtin("web_search")
class WebSearchTool(BaseTool):
    """Search using a configured backend without changing the caller's LLM."""

    supports_background = True

    def __init__(self, config=None):
        super().__init__(config=config)
        self.backend = "duckduckgo"
        self.codex_model = DEFAULT_CODEX_SEARCH_MODEL
        self.deepseek_model = DEFAULT_DEEPSEEK_MODEL
        self.fallback = "none"
        self.refresh_runtime_options(dict(self.config.extra))

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return titles, URLs, and snippets. Use to find sources. Not for reading one - use web_fetch."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def prompt_contribution(self) -> str | None:
        """Warn against repeat searches on the backend that charges for them."""
        if self.backend not in {"codex", "deepseek_api"}:
            return None
        return "Search once per question; retry only if results are incomplete."

    def runtime_option_schema(self) -> dict[str, dict[str, Any]]:
        disabled: dict[str, str] = {}
        if not has_api_key("deepseek"):
            disabled["deepseek"] = (
                "DeepSeek API key is not configured. Run: " "kt config key set deepseek"
            )
            disabled["deepseek_api"] = disabled["deepseek"]
        if not codex_search_available():
            disabled["codex"] = (
                "Codex subscription is not connected. Run: kt login codex"
            )
        return {
            "backend": {
                "type": "enum",
                "values": ["duckduckgo", "codex", "deepseek", "deepseek_api"],
                "default": "duckduckgo",
                "disabled_values": disabled,
                "labels": {
                    "deepseek_api": "DeepSeek Search (direct)",
                    "deepseek": "DeepSeek Responses (legacy)",
                },
                "doc": "deepseek_api uses Search directly; deepseek uses legacy Responses.",
            },
            "codex_model": {
                "type": "enum",
                "values": list(CODEX_SEARCH_MODELS),
                "default": DEFAULT_CODEX_SEARCH_MODEL,
                "doc": "Codex subscription model used for standalone web search.",
            },
            "deepseek_model": {
                "type": "enum",
                "values": list(DEEPSEEK_MODELS),
                "aliases": DEEPSEEK_MODEL_ALIASES,
                "default": DEFAULT_DEEPSEEK_MODEL,
                "doc": "Only used by legacy Responses search. Direct Search does not use a model.",
                "hint": "Legacy Responses search requires Pro; Flash users should select DeepSeek Search (direct).",
            },
            "need_content": {
                "type": "bool",
                "default": False,
                "doc": "Include bounded page content with deepseek_api results.",
            },
            "localization": {
                "type": "string",
                "default": "",
                "max_length": 45,
                "doc": "Optional IP address for deepseek_api localization; not a region code.",
            },
            "fallback": {
                "type": "enum",
                "values": ["none", "duckduckgo"],
                "default": "none",
                "doc": "Fallback for transient explicitly selected backend failures.",
            },
        }

    def refresh_runtime_options(self, options: dict[str, Any]) -> None:
        self.backend = str(options.get("backend", "duckduckgo"))
        self.codex_model = str(options.get("codex_model", DEFAULT_CODEX_SEARCH_MODEL))
        self.deepseek_model = str(options.get("deepseek_model", DEFAULT_DEEPSEEK_MODEL))
        self.deepseek_model = DEEPSEEK_MODEL_ALIASES.get(
            self.deepseek_model, self.deepseek_model
        )
        self.fallback = str(options.get("fallback", "none"))
        self.need_content = options.get("need_content", False)
        self.localization = options.get("localization", "")

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(error="No query provided. Usage: web_search(query='...')")

        try:
            max_results = integer_argument(
                args.get("max_results", args.get("limit", MAX_RESULTS)), "max_results"
            )
        except DeepSeekWebError as exc:
            return ToolResult(error=str(exc))
        region = args.get("region", "")

        started_at = time.monotonic()
        selected_backend, selected_fallback = self.backend, self.fallback
        try:
            if selected_backend == "deepseek_api":
                if region:
                    return ToolResult(
                        error="deepseek_api uses localization (IP), not region"
                    )
                need_content = args.get("need_content")
                need_content = boolean_argument(
                    self.need_content if need_content is None else need_content,
                    "need_content",
                )
                data = await DeepSeekWebClient().search(
                    query,
                    max_results,
                    need_content=need_content,
                    ori_question=args.get("ori_question"),
                    localization=self.localization,
                )
                response = render_search(data, query, max_results, need_content)
            else:
                need_content = args.get("need_content")
                if (
                    need_content is not None
                    and boolean_argument(need_content, "need_content")
                ) or args.get("ori_question") not in (None, ""):
                    return ToolResult(
                        error="need_content and ori_question require backend=deepseek_api"
                    )
                search_backend = self._backend(selected_backend)
                response = await search_backend.search(query, max_results, region)
        except DeepSeekWebTransientError as exc:
            response = await self._fallback_or_error(
                query, max_results, region, selected_backend, exc, selected_fallback
            )
            if isinstance(response, ToolResult):
                return response
        except DeepSeekWebError as exc:
            return ToolResult(error=str(exc))
        except CodexSearchOperationalError as exc:
            response = await self._fallback_or_error(
                query, max_results, region, selected_backend, exc, selected_fallback
            )
            if isinstance(response, ToolResult):
                return response
        except CodexSearchUnavailable as exc:
            return ToolResult(error=str(exc))
        except SearchBackendOperationalError as exc:
            response = await self._fallback_or_error(
                query, max_results, region, selected_backend, exc, selected_fallback
            )
            if isinstance(response, ToolResult):
                return response
        except SearchBackendError as exc:
            return ToolResult(error=str(exc))

        response.metadata["requested_backend"] = selected_backend
        response.metadata["duration_ms"] = round(
            (time.monotonic() - started_at) * 1000.0, 1
        )
        response.metadata.setdefault("source_count", len(response.sources))
        response.metadata["session_metadata"] = _search_session_metadata(
            response.metadata
        )
        logger.info(
            "Web search complete",
            query=query[:50],
            backend=response.metadata.get("backend", selected_backend),
        )
        return ToolResult(
            output=response.output,
            exit_code=0,
            metadata=response.metadata,
        )

    def _backend(self, selected: str | None = None):
        selected = selected or self.backend
        if selected == "codex":
            return CodexSubscriptionSearchBackend(self.codex_model)
        if selected == "duckduckgo":
            return DuckDuckGoSearchBackend()
        if selected == "deepseek":
            return DeepSeekSearchBackend(self.deepseek_model)
        raise SearchBackendUnavailable(f"Unknown web search backend: {selected!r}")

    async def _fallback_or_error(
        self,
        query: str,
        max_results: int,
        region: str,
        selected: str,
        error: Exception,
        fallback: str,
    ) -> SearchResponse | ToolResult:
        fallback_enabled = fallback == "duckduckgo"
        if not fallback_enabled or selected == "duckduckgo":
            return ToolResult(error=str(error))
        logger.warning(
            "Primary web search failed, using DuckDuckGo fallback",
            backend=selected,
            error=str(error),
        )
        try:
            response = await DuckDuckGoSearchBackend().search(
                query, max_results, region
            )
        except SearchBackendError as fallback_exc:
            return ToolResult(error=str(fallback_exc))
        response.metadata["fallback_from"] = selected
        response.metadata["fallback_reason"] = str(error)
        return response


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


def _search_session_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Select non-secret search diagnostics for session persistence."""
    keys = (
        "backend",
        "requested_backend",
        "model",
        "response_status",
        "citation_status",
        "annotation_count",
        "source_count",
        "verified_source_count",
        "action_source_count",
        "result_count",
        "truncated",
        "fallback_from",
        "fallback_reason",
        "duration_ms",
    )
    result = {key: metadata[key] for key in keys if key in metadata}
    for key in ("sources", "verified_sources", "consulted_sources"):
        sources = metadata.get(key)
        if isinstance(sources, list):
            result[key] = [
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
    actions = metadata.get("actions")
    if isinstance(actions, list):
        result["actions"] = [
            {
                str(key): value
                for key, value in action.items()
                if key in {"type", "query", "queries", "url", "pattern"}
            }
            for action in actions[:20]
            if isinstance(action, dict)
        ]
    return result
