"""Codex rate-limit / credits parser.

The Codex backend no longer exposes a dedicated ``/backend-api/codex/usage``
endpoint — that endpoint has been removed and is shielded by Cloudflare
when reached from non-browser clients. Rate limits are now delivered
**passively** on every chat-completion response through two channels:

1. **Response headers** (``x-codex-*`` family). Parsed by
   :func:`parse_all_rate_limits` / :func:`parse_rate_limit_for_limit`.
2. **Streaming SSE events** of type ``codex.rate_limits`` inside a
   completion response. Parsed by :func:`parse_rate_limit_event`.

Faithful port of ``codex-rs/codex-api/src/rate_limits.rs`` in the
upstream Codex source, with the same header naming and parsing rules.

There is no polling endpoint; you must capture this data from the
response of a real API call.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class RateLimitWindow:
    """One usage window (e.g. 5h primary or weekly secondary)."""

    used_percent: float
    window_minutes: int | None = None
    resets_at: int | None = None  # unix epoch seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "used_percent": self.used_percent,
            "window_minutes": self.window_minutes,
            "resets_at": self.resets_at,
        }


@dataclass
class CreditsSnapshot:
    """Credits metadata for accounts with credit-based billing."""

    has_credits: bool
    unlimited: bool
    balance: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_credits": self.has_credits,
            "unlimited": self.unlimited,
            "balance": self.balance,
        }


@dataclass
class RateLimitSnapshot:
    """One family of rate-limit state (typically ``codex`` default family)."""

    limit_id: str = "codex"
    limit_name: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: CreditsSnapshot | None = None
    plan_type: str | None = None
    rate_limit_reached_type: str | None = None

    def has_data(self) -> bool:
        """Whether this snapshot contains any displayable data."""
        return (
            self.primary is not None
            or self.secondary is not None
            or self.credits is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit_id": self.limit_id,
            "limit_name": self.limit_name,
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": self.secondary.to_dict() if self.secondary else None,
            "credits": self.credits.to_dict() if self.credits else None,
            "plan_type": self.plan_type,
            "rate_limit_reached_type": self.rate_limit_reached_type,
        }


@dataclass
class UsageSnapshot:
    """Everything captured from the most recent Codex response.

    Aggregates all rate-limit families plus any promo text the server
    sent. Produced by :func:`capture_from_headers` and stored in the
    module-level cache by :func:`set_cached`.
    """

    snapshots: list[RateLimitSnapshot] = field(default_factory=list)
    promo_message: str | None = None
    captured_at: float = 0.0  # unix seconds; set by the cache

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshots": [s.to_dict() for s in self.snapshots],
            "promo_message": self.promo_message,
            "captured_at": self.captured_at,
        }

    def is_empty(self) -> bool:
        return not any(s.has_data() for s in self.snapshots) and not self.promo_message


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def _normalize_limit_id(raw: str) -> str:
    """Turn any user-facing limit label into the canonical snake_case id."""
    return raw.strip().lower().replace("-", "_")


def _header_prefix(limit_id: str | None) -> str:
    """Build the ``x-<slug>`` prefix for a header family."""
    slug = (limit_id or "codex").strip().lower().replace("_", "-") or "codex"
    return f"x-{slug}"


def _ci_get(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    if name in headers:
        return headers[name]
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return None


def _parse_float(headers: Mapping[str, str], name: str) -> float | None:
    raw = _ci_get(headers, name)
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return v


def _parse_int(headers: Mapping[str, str], name: str) -> int | None:
    raw = _ci_get(headers, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_bool(headers: Mapping[str, str], name: str) -> bool | None:
    raw = _ci_get(headers, name)
    if raw is None:
        return None
    lower = raw.strip().lower()
    if lower in ("true", "1"):
        return True
    if lower in ("false", "0"):
        return False
    return None


def _parse_str(headers: Mapping[str, str], name: str) -> str | None:
    raw = _ci_get(headers, name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _parse_window(
    headers: Mapping[str, str],
    used_percent_header: str,
    window_minutes_header: str,
    resets_at_header: str,
) -> RateLimitWindow | None:
    """Build a single RateLimitWindow from its three headers.

    Returns None when the percent header is missing entirely, or when all
    three values are zero/empty (no actual data from the server).
    """
    used_percent = _parse_float(headers, used_percent_header)
    if used_percent is None:
        return None

    window_minutes = _parse_int(headers, window_minutes_header)
    resets_at = _parse_int(headers, resets_at_header)

    has_data = (
        used_percent != 0.0
        or (window_minutes is not None and window_minutes != 0)
        or resets_at is not None
    )
    if not has_data:
        return None
    return RateLimitWindow(
        used_percent=used_percent,
        window_minutes=window_minutes,
        resets_at=resets_at,
    )


def _parse_credits(headers: Mapping[str, str]) -> CreditsSnapshot | None:
    has_credits = _parse_bool(headers, "x-codex-credits-has-credits")
    unlimited = _parse_bool(headers, "x-codex-credits-unlimited")
    if has_credits is None or unlimited is None:
        return None
    balance = _parse_str(headers, "x-codex-credits-balance")
    return CreditsSnapshot(
        has_credits=has_credits, unlimited=unlimited, balance=balance
    )


def parse_rate_limit_for_limit(
    headers: Mapping[str, str], limit_id: str | None = None
) -> RateLimitSnapshot | None:
    """Parse one rate-limit family's headers.

    ``limit_id`` is the server-provided metered limit id (e.g. ``codex``,
    ``codex_other``, ``codex_bengalfox``). None → the default ``codex``
    family.
    """
    normalized = _normalize_limit_id(limit_id) if limit_id else "codex"
    prefix = _header_prefix(normalized)

    primary = _parse_window(
        headers,
        f"{prefix}-primary-used-percent",
        f"{prefix}-primary-window-minutes",
        f"{prefix}-primary-reset-at",
    )
    secondary = _parse_window(
        headers,
        f"{prefix}-secondary-used-percent",
        f"{prefix}-secondary-window-minutes",
        f"{prefix}-secondary-reset-at",
    )
    credits = _parse_credits(headers) if normalized == "codex" else None
    limit_name = _parse_str(headers, f"{prefix}-limit-name")

    return RateLimitSnapshot(
        limit_id=normalized,
        limit_name=limit_name,
        primary=primary,
        secondary=secondary,
        credits=credits,
    )


def _header_name_to_limit_id(header_name: str) -> str | None:
    """Recover a limit id from a header of the form ``x-<slug>-primary-used-percent``."""
    suffix = "-primary-used-percent"
    if not header_name.endswith(suffix):
        return None
    prefix = header_name[: -len(suffix)]
    if not prefix.startswith("x-"):
        return None
    limit = prefix[2:]
    if not limit:
        return None
    return _normalize_limit_id(limit)


def parse_all_rate_limits(headers: Mapping[str, str]) -> list[RateLimitSnapshot]:
    """Parse every rate-limit family advertised in the response headers.

    Always includes the default ``codex`` family (even if empty — callers
    can check :meth:`RateLimitSnapshot.has_data` themselves). Additional
    families are discovered by scanning for ``x-<slug>-primary-used-percent``
    header names.
    """
    snapshots: list[RateLimitSnapshot] = []

    default = parse_rate_limit_for_limit(headers, None)
    if default is not None:
        snapshots.append(default)

    # Discover additional families by header name.
    seen: set[str] = set()
    for name in headers.keys():
        lower = name.lower()
        limit_id = _header_name_to_limit_id(lower)
        if limit_id is None or limit_id == "codex":
            continue
        if limit_id in seen:
            continue
        seen.add(limit_id)

    for limit_id in sorted(seen):
        snap = parse_rate_limit_for_limit(headers, limit_id)
        if snap is not None and snap.has_data():
            snapshots.append(snap)

    return snapshots


def parse_promo_message(headers: Mapping[str, str]) -> str | None:
    """Extract the optional ``x-codex-promo-message`` header."""
    return _parse_str(headers, "x-codex-promo-message")


# ---------------------------------------------------------------------------
# Live usage-body parsing (GET /wham/usage)
# ---------------------------------------------------------------------------

# ``rate_limit_reached_type.type`` values the backend advertises; anything
# else collapses to None (see codex-rs RateLimitReachedKind).
_REACHED_TYPES = frozenset(
    {
        "rate_limit_reached",
        "workspace_owner_credits_depleted",
        "workspace_member_credits_depleted",
        "workspace_owner_usage_limit_reached",
        "workspace_member_usage_limit_reached",
    }
)


def _window_from_body(d: Any) -> RateLimitWindow | None:
    """Map a ``RateLimitWindowSnapshot`` object to a RateLimitWindow."""
    if not isinstance(d, dict):
        return None
    used = d.get("used_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    seconds = d.get("limit_window_seconds")
    window_minutes = (
        (int(seconds) + 59) // 60
        if isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and seconds > 0
        else None
    )
    reset_at = d.get("reset_at")
    return RateLimitWindow(
        used_percent=float(used),
        window_minutes=window_minutes,
        resets_at=(
            int(reset_at)
            if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool)
            else None
        ),
    )


def _credits_from_body(d: Any) -> CreditsSnapshot | None:
    if not isinstance(d, dict):
        return None
    has_credits = d.get("has_credits")
    unlimited = d.get("unlimited")
    if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
        return None
    balance = d.get("balance")
    return CreditsSnapshot(
        has_credits=has_credits,
        unlimited=unlimited,
        balance=str(balance) if balance is not None else None,
    )


def _reached_type_from_body(d: Any) -> str | None:
    if not isinstance(d, dict):
        return None
    kind = d.get("type")
    return kind if isinstance(kind, str) and kind in _REACHED_TYPES else None


def snapshots_from_usage_body(body: Mapping[str, Any]) -> list[RateLimitSnapshot]:
    """Parse a ``GET /wham/usage`` JSON body into rate-limit snapshots.

    The authoritative live counterpart of :func:`parse_all_rate_limits`:
    the default ``codex`` family comes from ``rate_limit`` + ``credits``,
    and each ``additional_rate_limits`` entry becomes an extra family.
    Faithful to codex-rs ``rate_limit_snapshots_from_payload``.
    """
    if not isinstance(body, Mapping):
        return []
    plan_type = body.get("plan_type")
    plan = plan_type if isinstance(plan_type, str) else None
    rate_limit = body.get("rate_limit")
    rate_limit = rate_limit if isinstance(rate_limit, dict) else {}

    snapshots = [
        RateLimitSnapshot(
            limit_id="codex",
            limit_name=None,
            primary=_window_from_body(rate_limit.get("primary_window")),
            secondary=_window_from_body(rate_limit.get("secondary_window")),
            credits=_credits_from_body(body.get("credits")),
            plan_type=plan,
            rate_limit_reached_type=_reached_type_from_body(
                body.get("rate_limit_reached_type")
            ),
        )
    ]

    additional = body.get("additional_rate_limits")
    if isinstance(additional, list):
        for entry in additional:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("metered_feature") or entry.get("limit_name")
            if not isinstance(raw_id, str) or not raw_id:
                continue
            limit_id = _normalize_limit_id(raw_id)
            if limit_id == "codex":
                continue
            rl = entry.get("rate_limit")
            rl = rl if isinstance(rl, dict) else {}
            name = entry.get("limit_name")
            snap = RateLimitSnapshot(
                limit_id=limit_id,
                limit_name=name if isinstance(name, str) else None,
                primary=_window_from_body(rl.get("primary_window")),
                secondary=_window_from_body(rl.get("secondary_window")),
                plan_type=plan,
            )
            if snap.has_data():
                snapshots.append(snap)
    return snapshots


# ---------------------------------------------------------------------------
# SSE event parsing
# ---------------------------------------------------------------------------


def parse_rate_limit_event(payload: str) -> RateLimitSnapshot | None:
    """Parse a ``codex.rate_limits`` streaming SSE event payload.

    The payload is a JSON string matching::

        {
          "type": "codex.rate_limits",
          "plan_type": "...",
          "metered_limit_name": "...",
          "rate_limits": {
            "primary":   {"used_percent": 12.5, "window_minutes": 300,  "reset_at": ...},
            "secondary": {"used_percent": 80.0, "window_minutes": 1440, "reset_at": ...}
          },
          "credits": {"has_credits": true, "unlimited": false, "balance": "42"}
        }

    Returns None when the payload is not a valid rate-limit event.
    """
    try:
        event = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") != "codex.rate_limits":
        return None

    def _window(d: Any) -> RateLimitWindow | None:
        if not isinstance(d, dict):
            return None
        used = d.get("used_percent")
        if not isinstance(used, (int, float)):
            return None
        win_min = d.get("window_minutes")
        reset = d.get("reset_at")
        return RateLimitWindow(
            used_percent=float(used),
            window_minutes=int(win_min) if isinstance(win_min, (int, float)) else None,
            resets_at=int(reset) if isinstance(reset, (int, float)) else None,
        )

    rate_limits = event.get("rate_limits") or {}
    primary = _window(rate_limits.get("primary"))
    secondary = _window(rate_limits.get("secondary"))

    credits_data = event.get("credits")
    credits: CreditsSnapshot | None = None
    if isinstance(credits_data, dict):
        credits = CreditsSnapshot(
            has_credits=bool(credits_data.get("has_credits", False)),
            unlimited=bool(credits_data.get("unlimited", False)),
            balance=(
                str(credits_data["balance"])
                if credits_data.get("balance") is not None
                else None
            ),
        )

    raw_limit_id = event.get("metered_limit_name") or event.get("limit_name")
    limit_id = (
        _normalize_limit_id(raw_limit_id) if isinstance(raw_limit_id, str) else "codex"
    )

    return RateLimitSnapshot(
        limit_id=limit_id,
        limit_name=None,
        primary=primary,
        secondary=secondary,
        credits=credits,
        plan_type=(
            str(event["plan_type"]) if isinstance(event.get("plan_type"), str) else None
        ),
    )


# ---------------------------------------------------------------------------
# Capture helper + process-level cache
# ---------------------------------------------------------------------------


def capture_from_headers(headers: Mapping[str, str]) -> UsageSnapshot:
    """One-shot helper: headers → UsageSnapshot ready for caching."""
    return UsageSnapshot(
        snapshots=parse_all_rate_limits(headers),
        promo_message=parse_promo_message(headers),
    )


_cached: UsageSnapshot | None = None


def set_cached(snapshot: UsageSnapshot, *, now: float | None = None) -> None:
    """Store the latest snapshot in the process cache.

    Skips storing when the snapshot contains no usable data — keeps the
    previous (useful) snapshot rather than overwriting it with noise
    from a response that didn't carry rate-limit headers.
    """
    global _cached
    if snapshot.is_empty():
        return
    if now is None:
        now = time.time()
    snapshot.captured_at = now
    _cached = snapshot


def get_cached() -> UsageSnapshot | None:
    """Read the latest cached snapshot, or None if nothing captured yet."""
    return _cached


def clear_cache() -> None:
    """Reset the cache (primarily for tests)."""
    global _cached
    _cached = None
