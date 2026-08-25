"""Redacted node-local status for reusable Grok subscription credentials."""

from typing import Any

from kohakuterrarium.llm.grok_auth import GrokTokens


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


__all__ = ["get_status"]
