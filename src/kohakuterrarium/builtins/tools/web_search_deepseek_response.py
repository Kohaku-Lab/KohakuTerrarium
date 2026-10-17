"""Normalize legacy DeepSeek Responses search output."""

import re
from typing import Any
from urllib.parse import urlparse

from kohakuterrarium.builtins.tools.web_search_types import (
    SearchBackendOperationalError,
    SearchBackendUnavailable,
    SearchResponse,
)


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
