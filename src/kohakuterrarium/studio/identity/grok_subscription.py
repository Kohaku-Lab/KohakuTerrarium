"""Redacted node-local Grok subscription status and live billing usage."""

from typing import Any

from kohakuterrarium.llm.grok_auth import GrokTokens
from kohakuterrarium.studio.identity.grok_account import get_usage

__all__ = ["get_status", "get_usage"]


def get_status() -> dict[str, Any]:
    """Return source names and expiry only; never serialize token values."""
    candidates = GrokTokens.load_bootstrap_candidates()
    if not candidates:
        return {"authenticated": False, "source": None, "sources": []}
    first = candidates[0]
    return {
        "authenticated": True,
        "source": first.source,
        "sources": [candidate.source for candidate in candidates],
        "expires_at": first.expires_at,
    }
