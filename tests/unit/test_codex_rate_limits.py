"""Unit tests for codex_rate_limits parser.

Mirrors the Rust-side test cases in ``codex-rs/codex-api/src/rate_limits.rs``
plus a few Python-specific ones (cache behaviour, SSE event parsing).
"""

import json

from kohakuterrarium.llm.codex_rate_limits import (
    CreditsSnapshot,
    RateLimitSnapshot,
    RateLimitWindow,
    UsageSnapshot,
    capture_from_headers,
    clear_cache,
    get_cached,
    parse_all_rate_limits,
    parse_promo_message,
    parse_rate_limit_event,
    parse_rate_limit_for_limit,
    set_cached,
)

# ---------------------------------------------------------------------------
# Header parsing — default "codex" family
# ---------------------------------------------------------------------------


class TestDefaultCodexFamily:
    def test_default_family_parses_primary(self):
        headers = {
            "x-codex-primary-used-percent": "12.5",
            "x-codex-primary-window-minutes": "60",
            "x-codex-primary-reset-at": "1704069000",
        }
        snap = parse_rate_limit_for_limit(headers, None)
        assert snap is not None
        assert snap.limit_id == "codex"
        assert snap.limit_name is None
        assert snap.primary == RateLimitWindow(
            used_percent=12.5, window_minutes=60, resets_at=1704069000
        )
        assert snap.secondary is None

    def test_default_family_parses_credits(self):
        headers = {
            "x-codex-credits-has-credits": "true",
            "x-codex-credits-unlimited": "false",
            "x-codex-credits-balance": "42.00",
        }
        snap = parse_rate_limit_for_limit(headers, None)
        assert snap is not None
        assert snap.credits == CreditsSnapshot(
            has_credits=True, unlimited=False, balance="42.00"
        )

    def test_default_family_no_headers_produces_empty_snapshot(self):
        snap = parse_rate_limit_for_limit({}, None)
        assert snap is not None
        assert snap.limit_id == "codex"
        assert snap.has_data() is False

    def test_case_insensitive_header_lookup(self):
        headers = {
            "X-Codex-Primary-Used-Percent": "25.0",
            "X-Codex-Primary-Window-Minutes": "300",
        }
        snap = parse_rate_limit_for_limit(headers, None)
        assert snap is not None
        assert snap.primary is not None
        assert snap.primary.used_percent == 25.0
        assert snap.primary.window_minutes == 300


# ---------------------------------------------------------------------------
# Secondary limit families
# ---------------------------------------------------------------------------


class TestSecondaryFamily:
    def test_codex_secondary_id_reads_its_headers(self):
        headers = {
            "x-codex-secondary-primary-used-percent": "80",
            "x-codex-secondary-primary-window-minutes": "1440",
            "x-codex-secondary-primary-reset-at": "1704074400",
        }
        snap = parse_rate_limit_for_limit(headers, "codex_secondary")
        assert snap is not None
        assert snap.limit_id == "codex_secondary"
        assert snap.primary == RateLimitWindow(
            used_percent=80.0, window_minutes=1440, resets_at=1704074400
        )
        assert snap.secondary is None

    def test_limit_name_header_is_surfaced(self):
        headers = {
            "x-codex-bengalfox-primary-used-percent": "80",
            "x-codex-bengalfox-limit-name": "gpt-5.2-codex-sonic",
        }
        snap = parse_rate_limit_for_limit(headers, "codex_bengalfox")
        assert snap is not None
        assert snap.limit_id == "codex_bengalfox"
        assert snap.limit_name == "gpt-5.2-codex-sonic"


# ---------------------------------------------------------------------------
# parse_all_rate_limits
# ---------------------------------------------------------------------------


class TestParseAll:
    def test_all_families_returned(self):
        headers = {
            "x-codex-primary-used-percent": "12.5",
            "x-codex-secondary-primary-used-percent": "80",
        }
        snapshots = parse_all_rate_limits(headers)
        assert len(snapshots) == 2
        assert snapshots[0].limit_id == "codex"
        assert snapshots[1].limit_id == "codex_secondary"

    def test_empty_headers_still_returns_codex_default(self):
        snapshots = parse_all_rate_limits({})
        assert len(snapshots) == 1
        assert snapshots[0].limit_id == "codex"
        assert snapshots[0].primary is None
        assert snapshots[0].secondary is None
        assert snapshots[0].credits is None

    def test_additional_families_without_data_are_dropped(self):
        # The slug is discovered via the used-percent header but the parsed
        # values are all zero — parse_all_rate_limits should NOT include it.
        headers = {
            "x-codex-primary-used-percent": "10.0",
            "x-codex-ghost-primary-used-percent": "0",
        }
        snapshots = parse_all_rate_limits(headers)
        # Only codex (default) returns — ghost had no data.
        assert [s.limit_id for s in snapshots] == ["codex"]


# ---------------------------------------------------------------------------
# Promo message
# ---------------------------------------------------------------------------


class TestPromoMessage:
    def test_promo_returned(self):
        assert parse_promo_message({"x-codex-promo-message": "Welcome!"}) == "Welcome!"

    def test_promo_empty_string_returns_none(self):
        assert parse_promo_message({"x-codex-promo-message": "   "}) is None

    def test_promo_missing_returns_none(self):
        assert parse_promo_message({}) is None


# ---------------------------------------------------------------------------
# SSE event parsing
# ---------------------------------------------------------------------------


class TestSSEEvent:
    def test_codex_rate_limits_event(self):
        payload = json.dumps(
            {
                "type": "codex.rate_limits",
                "plan_type": "plus",
                "metered_limit_name": "codex",
                "rate_limits": {
                    "primary": {
                        "used_percent": 33.3,
                        "window_minutes": 300,
                        "reset_at": 1704069000,
                    },
                    "secondary": {
                        "used_percent": 80.0,
                        "window_minutes": 1440,
                        "reset_at": 1704074400,
                    },
                },
                "credits": {
                    "has_credits": True,
                    "unlimited": False,
                    "balance": "42",
                },
            }
        )
        snap = parse_rate_limit_event(payload)
        assert snap is not None
        assert snap.limit_id == "codex"
        assert snap.plan_type == "plus"
        assert snap.primary is not None
        assert snap.primary.used_percent == 33.3
        assert snap.primary.window_minutes == 300
        assert snap.primary.resets_at == 1704069000
        assert snap.secondary is not None
        assert snap.secondary.used_percent == 80.0
        assert snap.credits == CreditsSnapshot(
            has_credits=True, unlimited=False, balance="42"
        )

    def test_non_rate_limit_event_returns_none(self):
        assert parse_rate_limit_event(json.dumps({"type": "response.delta"})) is None

    def test_malformed_json_returns_none(self):
        assert parse_rate_limit_event("not json") is None

    def test_empty_payload_returns_none(self):
        assert parse_rate_limit_event(json.dumps({})) is None


# ---------------------------------------------------------------------------
# Capture + cache
# ---------------------------------------------------------------------------


class TestCaptureAndCache:
    def setup_method(self):
        clear_cache()

    def test_capture_from_headers_stores_snapshots(self):
        headers = {
            "x-codex-primary-used-percent": "50",
            "x-codex-primary-window-minutes": "300",
        }
        snap = capture_from_headers(headers)
        assert isinstance(snap, UsageSnapshot)
        assert len(snap.snapshots) == 1
        assert snap.snapshots[0].primary is not None
        assert snap.snapshots[0].primary.used_percent == 50.0

    def test_capture_with_promo_message(self):
        headers = {"x-codex-promo-message": "Upgrade now!"}
        snap = capture_from_headers(headers)
        assert snap.promo_message == "Upgrade now!"

    def test_set_cached_ignores_empty_snapshot(self):
        clear_cache()
        empty = UsageSnapshot(
            snapshots=[RateLimitSnapshot(limit_id="codex")],  # no data
            promo_message=None,
        )
        set_cached(empty)
        assert get_cached() is None

    def test_set_cached_stores_non_empty(self):
        clear_cache()
        snap = UsageSnapshot(
            snapshots=[
                RateLimitSnapshot(
                    limit_id="codex",
                    primary=RateLimitWindow(used_percent=10.0),
                )
            ]
        )
        set_cached(snap, now=100.0)
        cached = get_cached()
        assert cached is not None
        assert cached.captured_at == 100.0
        assert cached.snapshots[0].primary.used_percent == 10.0

    def test_clear_cache_resets(self):
        set_cached(
            UsageSnapshot(
                snapshots=[
                    RateLimitSnapshot(
                        limit_id="codex",
                        primary=RateLimitWindow(used_percent=1.0),
                    )
                ]
            ),
            now=1.0,
        )
        assert get_cached() is not None
        clear_cache()
        assert get_cached() is None


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------


class TestToDict:
    def test_snapshot_to_dict_shape(self):
        snap = RateLimitSnapshot(
            limit_id="codex",
            limit_name="custom",
            primary=RateLimitWindow(used_percent=25.0, window_minutes=300, resets_at=1),
            secondary=None,
            credits=CreditsSnapshot(has_credits=True, unlimited=True, balance=None),
            plan_type="pro",
        )
        d = snap.to_dict()
        assert d["limit_id"] == "codex"
        assert d["limit_name"] == "custom"
        assert d["primary"] == {
            "used_percent": 25.0,
            "window_minutes": 300,
            "resets_at": 1,
        }
        assert d["secondary"] is None
        assert d["credits"] == {
            "has_credits": True,
            "unlimited": True,
            "balance": None,
        }
        assert d["plan_type"] == "pro"

    def test_usage_snapshot_is_empty(self):
        empty = UsageSnapshot()
        assert empty.is_empty()

        not_empty = UsageSnapshot(promo_message="x")
        assert not not_empty.is_empty()
