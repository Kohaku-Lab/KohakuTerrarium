"""Host-local Antigravity status and explicit account model discovery."""

import asyncio
import math
import time
from datetime import datetime

import httpx

from kohakuterrarium.llm.antigravity_auth import (
    AgyCredentials,
    AntigravityError,
    load_credential,
)
from kohakuterrarium.llm.antigravity_client import (
    BASE_URL,
    RequestError,
    discover_models,
    headers,
    load_project,
)
from kohakuterrarium.llm.recovery import RetryPolicy


def get_status() -> dict:
    return {**AgyCredentials.status(), "local_only": True, "credential_owner": "agy"}


async def refresh_credentials() -> dict:
    await AgyCredentials.ensure_fresh()
    return await asyncio.to_thread(get_status)


async def get_models() -> dict:
    return {"models": await discover_models()}


def empty_usage(status: str) -> dict:
    return {"status": status, "source": "live", "captured_at": None, "groups": []}


def _reset_epoch(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def parse_quota_summary(body) -> list[dict]:
    """Normalize explicit quota buckets without inferring windows or totals."""
    if not isinstance(body, dict):
        return []
    raw_groups = body.get("groups")
    groups = raw_groups if isinstance(raw_groups, list) else []
    buckets = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("buckets"), list):
            buckets.extend(group["buckets"])
    if not buckets and isinstance(body.get("buckets"), list):
        buckets = body["buckets"]
    normalized = {}
    for bucket in buckets:
        if not isinstance(bucket, dict) or bucket.get("disabled") is True:
            continue
        bucket_id = bucket.get("bucketId")
        if not isinstance(bucket_id, str) or not bucket_id or len(bucket_id) > 128:
            continue
        group_id = (
            "gemini"
            if bucket_id.startswith("gemini-")
            else "third_party" if bucket_id.startswith("3p-") else "other"
        )
        period = bucket.get("window")
        if period not in ("5h", "weekly", "daily"):
            period = "unknown"
        fraction = bucket.get("remainingFraction")
        used = None
        if (
            type(fraction) in (int, float)
            and 0 <= fraction <= 1
            and math.isfinite(fraction)
        ):
            used = round((1 - fraction) * 100, 6)
        windows = normalized.setdefault(group_id, {})
        windows[bucket_id] = {
            "id": bucket_id,
            "period": period,
            "used_percent": used,
            "resets_at": _reset_epoch(bucket.get("resetTime")),
        }
    order = {"5h": 0, "daily": 1, "weekly": 2, "unknown": 3}
    return [
        {
            "id": group_id,
            "windows": sorted(
                normalized[group_id].values(),
                key=lambda w: (order[w["period"]], w["id"]),
            ),
        }
        for group_id in ("gemini", "third_party", "other")
        if group_id in normalized
    ]


async def _fetch_usage(transport) -> dict:
    async with httpx.AsyncClient(
        transport=transport, timeout=15, follow_redirects=False
    ) as client:
        project, _, token = await load_project(client, RetryPolicy(max_retries=0))
        response = await client.post(
            BASE_URL + "retrieveUserQuotaSummary",
            headers=headers(token, "retrieveUserQuotaSummary"),
            json={"project": project},
        )
        if response.status_code in (401, 403):
            return empty_usage("auth_expired")
        if response.status_code != 200:
            return empty_usage("unavailable")
        current = await asyncio.to_thread(load_credential)
        if current.fingerprint != token.fingerprint:
            return empty_usage("auth_expired")
        if len(response.content) > 4_194_304:
            return empty_usage("no_data")
        try:
            groups = parse_quota_summary(response.json())
        except ValueError:
            return empty_usage("no_data")
        if not groups:
            return empty_usage("no_data")
        return {
            "status": "ok",
            "source": "live",
            "captured_at": time.time(),
            "groups": groups,
        }


async def get_usage(*, transport=None) -> dict:
    """Fetch host-local agy quota, with owner-managed renewal and no model calls."""
    try:
        return await asyncio.wait_for(_fetch_usage(transport), timeout=60)
    except RequestError as exc:
        return empty_usage(
            "auth_expired" if exc.status in (401, 403) else "unavailable"
        )
    except AntigravityError as exc:
        status = {
            "login_required": "not_logged_in",
            "unsupported_platform": "unsupported",
            "refresh_required": "auth_expired",
            "malformed_credential": "auth_expired",
            "ambiguous_sources": "auth_expired",
            "account_not_onboarded": "auth_expired",
            "credentials_changed_during_request": "auth_expired",
        }.get(exc.code, "unavailable")
        return empty_usage(status)
    except (httpx.HTTPError, asyncio.TimeoutError):
        return empty_usage("unavailable")
