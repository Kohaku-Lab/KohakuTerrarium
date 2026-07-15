"""Codex account — live usage + rate-limit-reset-credit client.

Talks to the ChatGPT backend WHAM endpoints directly with the Codex
access-token bearer and ``ChatGPT-Account-Id`` header. These account operations
bypass :class:`~kohakuterrarium.llm.codex_provider.CodexOAuthProvider` and the
Responses API so reading usage or redeeming a reset credit cannot consume a
model round. The endpoint and outcome semantics match codex-rs.

The ``transport`` kwarg on every request lets tests inject an
``httpx.MockTransport``; production leaves it ``None`` (real network).
"""

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.codex_claims import account_id_from_id_token
from kohakuterrarium.llm.codex_rate_limits import snapshots_from_usage_body
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _kt_version() -> str:
    # Studio imports this module before the package initializer binds
    # ``kohakuterrarium.__version__``, so distribution metadata is authoritative.
    try:
        return version("kohakuterrarium")
    except PackageNotFoundError:
        return "0.0.0"


CHATGPT_BASE_URL = "https://chatgpt.com/backend-api"
USAGE_URL = f"{CHATGPT_BASE_URL}/wham/usage"
RESET_CREDITS_URL = f"{CHATGPT_BASE_URL}/wham/rate-limit-reset-credits"
CONSUME_URL = f"{RESET_CREDITS_URL}/consume"

# The WHAM endpoints require the first-party ``codex_cli_rs`` user-agent prefix.
CODEX_USER_AGENT = f"codex_cli_rs/{_kt_version()} (KohakuTerrarium)"

USAGE_TIMEOUT = 10.0
RESET_CREDITS_TIMEOUT = 5.0
CONSUME_TIMEOUT = 10.0

# Normalized keys accept snake_case, kebab-case, and camelCase backend codes.
# ``alreadyRedeemed`` represents an idempotent retry and remains a success.
_CONSUME_OUTCOMES = {
    "reset": "reset",
    "nothingtoreset": "nothingToReset",
    "nocredit": "noCredit",
    "alreadyredeemed": "alreadyRedeemed",
}


@dataclass
class ResetCredit:
    """One earned rate-limit reset credit (from the details endpoint)."""

    id: str
    reset_type: str
    status: str
    granted_at: str
    expires_at: str | None = None
    title: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reset_type": self.reset_type,
            "status": self.status,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "title": self.title,
            "description": self.description,
        }


def _headers(tokens: CodexTokens) -> dict[str, str]:
    """Build the access-token, account-id, and Codex user-agent headers.

    The ID token is used only to derive a missing account ID; it is never sent
    as the bearer credential.
    """
    account_id = tokens.account_id or account_id_from_id_token(tokens.id_token)
    headers = {
        "Authorization": f"Bearer {tokens.access_token}",
        "User-Agent": CODEX_USER_AGENT,
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def _map_consume_code(code: str) -> str:
    key = code.replace("_", "").replace("-", "").lower()
    return _CONSUME_OUTCOMES.get(key, code)


async def fetch_usage(
    tokens: CodexTokens,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = USAGE_TIMEOUT,
) -> dict[str, Any]:
    """Fetch usage snapshots and the available reset-credit count.

    The usage response's ``available_count`` is suitable as the fallback for
    :func:`list_reset_credits` when the detail endpoint fails.
    """
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        resp = await client.get(USAGE_URL, headers=_headers(tokens))
        resp.raise_for_status()
        body = resp.json()
    if not isinstance(body, dict):
        body = {}
    reset = body.get("rate_limit_reset_credits")
    available = reset.get("available_count") if isinstance(reset, dict) else None
    return {
        "snapshots": [snap.to_dict() for snap in snapshots_from_usage_body(body)],
        "available_count": (
            int(available)
            if isinstance(available, (int, float)) and not isinstance(available, bool)
            else None
        ),
    }


async def list_reset_credits(
    tokens: CodexTokens,
    *,
    fallback_count: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = RESET_CREDITS_TIMEOUT,
) -> dict[str, Any]:
    """Fetch detailed reset credits and their available count.

    Any endpoint or decoding failure returns ``fallback_count`` with an empty
    credit list, preserving the usage response as the degraded source of truth.
    """
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            resp = await client.get(RESET_CREDITS_URL, headers=_headers(tokens))
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.warning(
            "Codex reset-credit details failed; using count fallback",
            error=str(exc),
        )
        return {"available_count": fallback_count, "credits": []}
    if not isinstance(body, dict):
        return {"available_count": fallback_count, "credits": []}
    credits: list[dict[str, Any]] = []
    raw = body.get("credits")
    if isinstance(raw, list):
        for entry in raw:
            credit = _reset_credit_from_body(entry)
            if credit is not None:
                credits.append(credit.to_dict())
    available = body.get("available_count")
    return {
        "available_count": (
            int(available)
            if isinstance(available, (int, float)) and not isinstance(available, bool)
            else fallback_count
        ),
        "credits": credits,
    }


async def consume_reset_credit(
    tokens: CodexTokens,
    idempotency_key: str,
    credit_id: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = CONSUME_TIMEOUT,
) -> str:
    """Redeem a reset credit and return its canonical outcome code.

    ``idempotency_key`` must be non-empty so retries remain idempotent.
    ``credit_id`` targets a specific credit when supplied; pass ``None``
    to redeem any credit. An explicit empty string is invalid.
    """
    if not idempotency_key:
        raise ValueError("idempotency_key must not be empty")
    if credit_id is not None and not credit_id:
        raise ValueError("credit_id must not be empty")
    payload: dict[str, str] = {"redeem_request_id": idempotency_key}
    if credit_id:
        payload["credit_id"] = credit_id
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        resp = await client.post(CONSUME_URL, headers=_headers(tokens), json=payload)
        resp.raise_for_status()
        body = resp.json()
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str):
        raise RuntimeError(f"consume response missing code: {body!r}")
    return _map_consume_code(code)


def _reset_credit_from_body(entry: Any) -> ResetCredit | None:
    if not isinstance(entry, dict):
        return None
    cid = entry.get("id")
    if not isinstance(cid, str) or not cid:
        return None
    return ResetCredit(
        id=cid,
        reset_type=str(entry.get("reset_type") or ""),
        status=str(entry.get("status") or ""),
        granted_at=str(entry.get("granted_at") or ""),
        expires_at=_opt_str(entry.get("expires_at")),
        title=_opt_str(entry.get("title")),
        description=_opt_str(entry.get("description")),
    )


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "CONSUME_URL",
    "RESET_CREDITS_URL",
    "USAGE_URL",
    "ResetCredit",
    "consume_reset_credit",
    "fetch_usage",
    "list_reset_credits",
]
