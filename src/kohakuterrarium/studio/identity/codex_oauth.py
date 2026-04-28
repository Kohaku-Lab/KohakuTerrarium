"""Codex OAuth — login flow, status, usage snapshot.

Wraps :mod:`kohakuterrarium.llm.codex_auth` and
:mod:`kohakuterrarium.llm.codex_rate_limits`. The CLI uses
:func:`run_login_blocking` (sync wrapper around the async oauth flow);
the HTTP layer uses :func:`login_async` directly.
"""

import asyncio
from typing import Any

from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login, refresh_tokens
from kohakuterrarium.llm.codex_rate_limits import get_cached as _get_cached_usage


async def login_async() -> dict[str, Any]:
    """Run the Codex OAuth flow and return ``{status, expires_at}``."""
    tokens = await oauth_login()
    return {"status": "ok", "expires_at": tokens.expires_at}


def run_login_blocking() -> int:
    """CLI entry — perform OAuth login, return CLI exit code."""
    existing = CodexTokens.load()
    if existing and not existing.is_expired():
        print("Already authenticated (tokens valid).")
        path = (
            existing._path
            if hasattr(existing, "_path")
            else "~/.kohakuterrarium/codex-auth.json"
        )
        print(f"Token path: {path}")
        answer = input("Re-authenticate? [y/N]: ").strip().lower()
        if answer != "y":
            return 0

    print("Authenticating with OpenAI (ChatGPT subscription)...")
    print()
    try:
        asyncio.run(oauth_login())
        print()
        print("Authentication successful!")
        print("Tokens saved to: ~/.kohakuterrarium/codex-auth.json")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def get_status() -> dict[str, Any]:
    """Return ``{authenticated, expired?}`` for the current Codex tokens."""
    tokens = CodexTokens.load()
    if not tokens:
        return {"authenticated": False}
    return {"authenticated": True, "expired": tokens.is_expired()}


async def get_usage_async() -> dict[str, Any]:
    """Return the most-recent captured Codex rate-limit / credits snapshot.

    Response shape mirrors the legacy HTTP route — see investigation
    §2.3 row "Codex usage" for the contract.
    """
    tokens = CodexTokens.load()
    if not tokens:
        return {
            "status": "not_logged_in",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
        }
    if tokens.is_expired():
        # The error type is preserved here so the route layer can map it
        # to a 401 response.
        await refresh_tokens(tokens)

    cached = _get_cached_usage()
    if cached is None or cached.is_empty():
        return {
            "status": "no_data_yet",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
        }
    return {
        "status": "ok",
        "captured_at": cached.captured_at,
        "snapshots": [snap.to_dict() for snap in cached.snapshots],
        "promo_message": cached.promo_message,
    }
