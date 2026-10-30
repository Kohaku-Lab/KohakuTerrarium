"""Shared search results and backend errors."""

from dataclasses import dataclass, field
from typing import Any


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
