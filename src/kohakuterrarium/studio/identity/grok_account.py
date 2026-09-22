"""Live Grok CLI billing usage. Never calls a model or caches results.

Numeric percents and prepaid credits are preserved as finite numbers, including
values above 100. Booleans, nulls, and non-finite values are rejected rather
than coerced to zero. The billing origin is fixed and redirects are not followed.
"""

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from kohakuterrarium.llm.grok_auth import (
    GROK_CLI_SOURCE,
    GrokAuthError,
    GrokToken,
    GrokTokens,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
BILLING_TIMEOUT = 10.0

_PERIODS = {
    "USAGE_PERIOD_TYPE_WEEKLY": "weekly",
    "USAGE_PERIOD_TYPE_MONTHLY": "monthly",
}


class GrokBillingError(Exception):
    """A redacted billing failure. ``kind`` is auth, unavailable, or no_data."""

    def __init__(self, kind: str, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        label = f"HTTP {status_code}" if status_code is not None else kind
        super().__init__(f"Grok billing {label}")


def empty_usage(status: str, *, credential_source: str | None = None) -> dict[str, Any]:
    """Return a non-ok payload with no captured window or balance."""
    return {
        "status": status,
        "source": "live",
        "credential_source": credential_source,
        "captured_at": None,
        "window": None,
        "products": [],
        "prepaid_balance": None,
    }


def parse_billing(body: Any) -> dict[str, Any] | None:
    """Normalize a billing body, or return None when usage cannot be trusted."""
    config = _config(body)
    if config is None:
        return None
    used, period, resets_at = _usage(config)
    if used is None or period is None:
        return None
    balance = _balance(config.get("prepaidBalance"))
    if balance is _INVALID:
        return None
    return {
        "window": {
            "period": period,
            "used_percent": used,
            "resets_at": resets_at,
        },
        "products": _products(config.get("productUsage")),
        "prepaid_balance": balance,
    }


async def fetch_billing(
    token: GrokToken,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = BILLING_TIMEOUT,
) -> dict[str, Any]:
    """GET the fixed CLI billing endpoint with the owner's access headers."""
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token.access_token}",
        **token.extra_headers,
    }
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            response = await client.get(BILLING_URL, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Grok billing request failed", error=type(exc).__name__)
        raise GrokBillingError("unavailable") from exc
    if response.is_redirect or response.status_code >= 500:
        logger.warning("Grok billing unavailable", status=response.status_code)
        raise GrokBillingError("unavailable", status_code=response.status_code)
    if response.status_code in {401, 403}:
        logger.warning("Grok billing rejected credentials", status=response.status_code)
        raise GrokBillingError("auth", status_code=response.status_code)
    if response.status_code != 200:
        logger.warning("Grok billing unavailable", status=response.status_code)
        raise GrokBillingError("unavailable", status_code=response.status_code)
    try:
        body = response.json()
    except ValueError as exc:
        logger.warning("Grok billing body was not JSON")
        raise GrokBillingError("no_data", status_code=response.status_code) from exc
    parsed = parse_billing(body)
    if parsed is None:
        raise GrokBillingError("no_data", status_code=response.status_code)
    return parsed


async def get_usage() -> dict[str, Any]:
    """Read CLI billing once, retrying a single forced refresh after HTTP 401.

    OpenCode credentials are not sent: only the Grok CLI endpoint is verified.
    Discovery and version checks stay off the event loop.
    """
    try:
        candidates = await asyncio.to_thread(GrokTokens.load_bootstrap_candidates)
        cli = next(
            (token for token in candidates if token.source == GROK_CLI_SOURCE), None
        )
        if cli is None:
            cli = await asyncio.to_thread(GrokTokens.load_cli_candidate)
    except Exception as exc:
        logger.warning("Grok credential discovery failed", error=type(exc).__name__)
        return empty_usage("unavailable")
    if cli is None:
        if candidates:
            return empty_usage("unsupported", credential_source=candidates[0].source)
        return empty_usage("not_logged_in")

    try:
        token = await GrokTokens.ensure_fresh_cli()
    except GrokAuthError as exc:
        return _auth_failure(exc)
    except Exception as exc:
        logger.warning("Grok CLI refresh failed", error=type(exc).__name__)
        return empty_usage("unavailable", credential_source=GROK_CLI_SOURCE)
    if token is None or token.is_expired():
        return empty_usage("auth_expired", credential_source=GROK_CLI_SOURCE)
    try:
        return await _live_usage(token)
    except GrokBillingError as exc:
        if exc.kind == "auth" and exc.status_code == 401:
            return await _retry_after_unauthorized()
        return _failure(exc)


async def _retry_after_unauthorized() -> dict[str, Any]:
    """Force one owner-managed refresh and retry only with a new valid token."""
    try:
        refreshed = await GrokTokens.ensure_fresh_cli(force=True)
    except GrokAuthError as exc:
        return _auth_failure(exc)
    except Exception as exc:
        logger.warning("Grok CLI refresh failed", error=type(exc).__name__)
        return empty_usage("unavailable", credential_source=GROK_CLI_SOURCE)
    if refreshed is None or refreshed.is_expired():
        return empty_usage("auth_expired", credential_source=GROK_CLI_SOURCE)
    try:
        return await _live_usage(refreshed)
    except GrokBillingError as exc:
        return _failure(exc)


async def _live_usage(token: GrokToken) -> dict[str, Any]:
    parsed = await fetch_billing(token)
    return {
        "status": "ok",
        "source": "live",
        "credential_source": token.source,
        "captured_at": time.time(),
        **parsed,
    }


def _failure(exc: GrokBillingError) -> dict[str, Any]:
    status = {
        "auth": "auth_expired",
        "unavailable": "unavailable",
        "no_data": "no_data",
    }.get(exc.kind, "unavailable")
    logger.warning("Grok usage unavailable", status=status)
    return empty_usage(status, credential_source=GROK_CLI_SOURCE)


def _auth_failure(exc: GrokAuthError) -> dict[str, Any]:
    status = "auth_expired" if exc.kind == "auth_expired" else "unavailable"
    logger.warning("Grok usage unavailable", status=status)
    return empty_usage(status, credential_source=GROK_CLI_SOURCE)


def _config(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    root = body.get("result", body)
    if not isinstance(root, dict):
        return None
    config = root.get("config", root)
    return config if isinstance(config, dict) else None


def _finite_number(value: Any) -> float | None:
    """Accept only finite int/float values. Bool is rejected, not coerced."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


_INVALID = object()


def _balance(value: Any) -> float | None | object:
    """Return a raw credit number, None when absent, or invalid when corrupt."""
    if value is None:
        return None
    if isinstance(value, dict):
        if "val" not in value:
            return None
        number = _finite_number(value.get("val"))
        if number is None or number < 0:
            return _INVALID
        return number
    number = _finite_number(value)
    if number is None or number < 0:
        return _INVALID
    return number


def _usage(config: dict[str, Any]) -> tuple[float | None, str | None, float | None]:
    """Resolve a percent without turning a typed window into a legacy ratio."""
    period, resets_at, invalid_period = _window(config)
    if invalid_period:
        return None, None, None
    if "creditUsagePercent" in config:
        percent = _finite_number(config["creditUsagePercent"])
        if percent is None or percent < 0 or period is None:
            return None, None, None
        return percent, period, resets_at
    if _period_type(config) is not None:
        return None, None, None
    ratio = _monthly_ratio(config)
    if ratio is None:
        return None, None, None
    return ratio, "monthly", resets_at


def _monthly_ratio(config: dict[str, Any]) -> float | None:
    """Return used/limit percent only when both legacy monthly numbers are valid."""
    if "monthlyLimit" not in config or "used" not in config:
        return None
    limit = _wrapped_number(config.get("monthlyLimit"))
    used = _wrapped_number(config.get("used"))
    if limit is None or used is None or limit <= 0 or used < 0:
        return None
    return _finite_number(used / limit * 100)


def _wrapped_number(value: Any) -> float | None:
    raw = value.get("val") if isinstance(value, dict) else value
    return _finite_number(raw)


def _period_type(config: dict[str, Any]) -> str | None:
    period_raw = config.get("currentPeriod")
    if not isinstance(period_raw, dict):
        return None
    period_type = period_raw.get("type")
    return period_type if isinstance(period_type, str) else None


def _window(config: dict[str, Any]) -> tuple[str | None, float | None, bool]:
    period_type = _period_type(config)
    period_raw = config.get("currentPeriod")
    end = period_raw.get("end") if isinstance(period_raw, dict) else None
    if end is None:
        end = config.get("billingPeriodEnd")
    resets_at = _epoch(end) if end is not None else None
    if end is not None and resets_at is None:
        return None, None, True
    if isinstance(period_type, str) and period_type in _PERIODS:
        return _PERIODS[period_type], resets_at, False
    if period_type is None or isinstance(period_type, str):
        return "unknown", resets_at, False
    return None, None, True


def _epoch(value: Any) -> float | None:
    """Normalize an ISO-8601 timestamp to epoch seconds. Naive values are UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _products(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    products: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = entry.get("product")
        used = _finite_number(entry.get("usagePercent"))
        if not isinstance(name, str) or not name.strip() or used is None or used < 0:
            continue
        products.append({"name": name.strip(), "used_percent": used})
    return products


__all__ = [
    "BILLING_URL",
    "GrokBillingError",
    "empty_usage",
    "fetch_billing",
    "get_usage",
    "parse_billing",
]
