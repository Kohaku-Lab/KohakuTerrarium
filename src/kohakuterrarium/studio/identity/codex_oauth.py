"""Codex OAuth — login flow, status, usage snapshot.

Wraps :mod:`kohakuterrarium.llm.codex_auth` and
:mod:`kohakuterrarium.llm.codex_rate_limits`. The CLI uses
:func:`run_login_blocking` (sync wrapper around the async oauth flow);
the HTTP layer uses :func:`login_async` directly.
"""

import asyncio
import time
import uuid
from typing import Any

from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login, refresh_tokens
from kohakuterrarium.llm.codex_rate_limits import get_cached as _get_cached_usage
from kohakuterrarium.studio.identity.codex_account import (
    consume_reset_credit,
    fetch_usage,
    list_reset_credits,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_EMPTY_RESET_CREDITS: dict[str, Any] = {"available_count": None, "credits": []}


async def login_async(on_device_code=None, open_browser: bool = True) -> dict[str, Any]:
    """Run the Codex OAuth flow and return ``{status, expires_at}``.

    ``on_device_code`` is forwarded to :func:`oauth_login` so the
    SSE-streaming route in ``api/routes/identity/codex.py`` can push
    the verification URL + user code to the frontend modal as soon
    as the device-code flow obtains them.

    ``open_browser`` is forwarded too: the SSE route passes ``False``
    because the user already has the modal driving their interaction;
    auto-popping a system browser on the server's machine is
    redundant (web UI = same machine) and on Android Chaquopy /
    headless boxes actively harmful.  The CLI ``run_login_blocking``
    path keeps the default ``True`` since CLI users expect an
    auto-opened browser.
    """
    tokens = await oauth_login(on_device_code=on_device_code, open_browser=open_browser)
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
    """Return the Codex rate-limit / credits snapshot for the host account.

    Authoritative source is a LIVE ``GET /wham/usage`` (no model round);
    the passive header/SSE cache is only a fallback when the live fetch
    fails.  ``reset_credits`` lists the earned rate-limit reset credits
    ready to redeem.  ``source`` is ``"live"`` or ``"cache"``.
    """
    tokens = CodexTokens.load()
    if not tokens:
        return {
            "status": "not_logged_in",
            "source": "live",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
            "reset_credits": dict(_EMPTY_RESET_CREDITS),
        }
    if tokens.is_expired():
        # The error type is preserved here so the route layer can map it
        # to a 401 response.
        tokens = await refresh_tokens(tokens)

    try:
        usage = await fetch_usage(tokens)
    except Exception as exc:
        logger.warning(
            "Codex live usage fetch failed; using passive cache", error=str(exc)
        )
        return _cached_usage_response()

    reset_credits = await list_reset_credits(
        tokens, fallback_count=usage.get("available_count")
    )
    cached = _get_cached_usage()
    return {
        "status": "ok",
        "source": "live",
        "captured_at": time.time(),
        "snapshots": usage["snapshots"],
        "promo_message": cached.promo_message if cached else None,
        "reset_credits": reset_credits,
    }


def _cached_usage_response() -> dict[str, Any]:
    """Fallback payload built from the passive rate-limit header cache."""
    cached = _get_cached_usage()
    if cached is None or cached.is_empty():
        return {
            "status": "no_data_yet",
            "source": "cache",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
            "reset_credits": dict(_EMPTY_RESET_CREDITS),
        }
    return {
        "status": "ok",
        "source": "cache",
        "captured_at": cached.captured_at,
        "snapshots": [snap.to_dict() for snap in cached.snapshots],
        "promo_message": cached.promo_message,
        "reset_credits": dict(_EMPTY_RESET_CREDITS),
    }


async def consume_reset_credit_async(
    idempotency_key: str | None = None,
    credit_id: str | None = None,
) -> dict[str, Any]:
    """Redeem a rate-limit reset credit for the host account.

    Generates a stable ``idempotency_key`` when the caller omits one so a
    single logical redeem stays idempotent across transport retries.
    Returns ``{outcome, idempotency_key}`` where ``outcome`` is one of
    ``reset|nothingToReset|noCredit|alreadyRedeemed``.  Raises
    :class:`PermissionError` when no Codex account is authenticated so
    the route can map it to a 401.
    """
    tokens = CodexTokens.load()
    if not tokens:
        raise PermissionError("Codex account is not authenticated")
    if tokens.is_expired():
        tokens = await refresh_tokens(tokens)
    key = idempotency_key or uuid.uuid4().hex
    outcome = await consume_reset_credit(tokens, key, credit_id)
    return {"outcome": outcome, "idempotency_key": key}
