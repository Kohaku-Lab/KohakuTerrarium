"""Unit tests for ``llm/codex_rate_limits.py`` — rate-limit parsing.

Behavior-first: assert the exact RateLimitWindow / RateLimitSnapshot /
CreditsSnapshot values parsed from headers and SSE payloads, the
no-data → None rules, multi-family discovery, and the process cache's
"don't overwrite useful data with noise" invariant.
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


class TestParseRateLimitForLimit:
    def test_primary_window_parsed_from_headers(self):
        headers = {
            "x-codex-primary-used-percent": "42.5",
            "x-codex-primary-window-minutes": "300",
            "x-codex-primary-reset-at": "1700000000",
        }
        snap = parse_rate_limit_for_limit(headers)
        assert snap.limit_id == "codex"
        assert snap.primary == RateLimitWindow(
            used_percent=42.5, window_minutes=300, resets_at=1700000000
        )
        assert snap.secondary is None

    def test_all_zero_window_returns_none(self):
        headers = {
            "x-codex-primary-used-percent": "0",
            "x-codex-primary-window-minutes": "0",
        }
        snap = parse_rate_limit_for_limit(headers)
        # zero percent + zero window + no reset → no real data
        assert snap.primary is None

    def test_missing_percent_header_yields_no_window(self):
        snap = parse_rate_limit_for_limit({"x-codex-primary-window-minutes": "300"})
        assert snap.primary is None

    def test_case_insensitive_header_lookup(self):
        headers = {"X-Codex-Primary-Used-Percent": "10.0"}
        snap = parse_rate_limit_for_limit(headers)
        assert snap.primary is not None
        assert snap.primary.used_percent == 10.0

    def test_credits_only_parsed_for_codex_family(self):
        headers = {
            "x-codex-credits-has-credits": "true",
            "x-codex-credits-unlimited": "false",
            "x-codex-credits-balance": "42",
        }
        snap = parse_rate_limit_for_limit(headers, "codex")
        assert snap.credits == CreditsSnapshot(
            has_credits=True, unlimited=False, balance="42"
        )
        # a non-codex family never gets credits parsed
        other = parse_rate_limit_for_limit(headers, "codex_other")
        assert other.credits is None

    def test_limit_name_header_captured(self):
        snap = parse_rate_limit_for_limit({"x-codex-limit-name": "Pro plan"})
        assert snap.limit_name == "Pro plan"

    def test_invalid_float_header_ignored(self):
        snap = parse_rate_limit_for_limit({"x-codex-primary-used-percent": "notnum"})
        assert snap.primary is None

    def test_nan_float_header_rejected(self):
        snap = parse_rate_limit_for_limit({"x-codex-primary-used-percent": "nan"})
        assert snap.primary is None

    def test_limit_id_normalised_to_snake_case(self):
        snap = parse_rate_limit_for_limit({}, "Codex-Other")
        assert snap.limit_id == "codex_other"

    def test_invalid_int_window_minutes_ignored(self):
        # used-percent is valid so the window survives, but a junk
        # window-minutes header is parsed as None rather than crashing
        snap = parse_rate_limit_for_limit(
            {
                "x-codex-primary-used-percent": "10",
                "x-codex-primary-window-minutes": "notanint",
            }
        )
        assert snap.primary.window_minutes is None

    def test_unrecognised_bool_credits_header_yields_no_credits(self):
        snap = parse_rate_limit_for_limit(
            {
                "x-codex-credits-has-credits": "maybe",
                "x-codex-credits-unlimited": "false",
            }
        )
        # an unparseable bool means the credits block can't be built
        assert snap.credits is None


class TestParseAllRateLimits:
    def test_default_codex_family_always_included(self):
        snaps = parse_all_rate_limits({})
        assert len(snaps) == 1
        assert snaps[0].limit_id == "codex"

    def test_additional_families_discovered_by_header_name(self):
        headers = {
            "x-codex-primary-used-percent": "10",
            "x-codex-bengalfox-primary-used-percent": "55",
            "x-codex-bengalfox-primary-window-minutes": "60",
        }
        snaps = parse_all_rate_limits(headers)
        ids = {s.limit_id for s in snaps}
        assert "codex" in ids and "codex_bengalfox" in ids

    def test_discovered_family_without_data_dropped(self):
        # the header advertises the family but all values are zero
        headers = {"x-codex-empty-primary-used-percent": "0"}
        snaps = parse_all_rate_limits(headers)
        assert {s.limit_id for s in snaps} == {"codex"}

    def test_duplicate_family_headers_discovered_once(self):
        # two headers for the same family must not double-count it
        headers = {
            "x-codex-foo-primary-used-percent": "20",
            "X-CODEX-FOO-PRIMARY-USED-PERCENT": "20",
            "x-codex-foo-primary-window-minutes": "60",
        }
        snaps = parse_all_rate_limits(headers)
        foo = [s for s in snaps if s.limit_id == "codex_foo"]
        assert len(foo) == 1

    def test_header_without_x_prefix_not_treated_as_family(self):
        # a malformed header that ends in the suffix but lacks the x- prefix
        headers = {"codex-bar-primary-used-percent": "30"}
        snaps = parse_all_rate_limits(headers)
        assert {s.limit_id for s in snaps} == {"codex"}


class TestParsePromoMessage:
    def test_promo_header_extracted(self):
        assert parse_promo_message({"x-codex-promo-message": "Upgrade!"}) == "Upgrade!"

    def test_missing_promo_header_returns_none(self):
        assert parse_promo_message({}) is None

    def test_whitespace_only_promo_returns_none(self):
        assert parse_promo_message({"x-codex-promo-message": "   "}) is None


class TestParseRateLimitEvent:
    def test_valid_event_parsed(self):
        payload = json.dumps(
            {
                "type": "codex.rate_limits",
                "plan_type": "pro",
                "metered_limit_name": "codex",
                "rate_limits": {
                    "primary": {
                        "used_percent": 12.5,
                        "window_minutes": 300,
                        "reset_at": 1700000000,
                    },
                    "secondary": {"used_percent": 80.0, "window_minutes": 1440},
                },
                "credits": {"has_credits": True, "unlimited": False, "balance": 7},
            }
        )
        snap = parse_rate_limit_event(payload)
        assert snap.plan_type == "pro"
        assert snap.primary == RateLimitWindow(12.5, 300, 1700000000)
        assert snap.secondary == RateLimitWindow(80.0, 1440, None)
        assert snap.credits == CreditsSnapshot(True, False, "7")

    def test_non_rate_limit_event_returns_none(self):
        assert parse_rate_limit_event(json.dumps({"type": "other"})) is None

    def test_invalid_json_returns_none(self):
        assert parse_rate_limit_event("{not json") is None

    def test_non_dict_payload_returns_none(self):
        assert parse_rate_limit_event(json.dumps([1, 2])) is None

    def test_window_with_non_numeric_used_percent_dropped(self):
        payload = json.dumps(
            {
                "type": "codex.rate_limits",
                "rate_limits": {"primary": {"used_percent": "bad"}},
            }
        )
        snap = parse_rate_limit_event(payload)
        assert snap.primary is None


class TestRateLimitSnapshotHelpers:
    def test_has_data_true_when_any_window_present(self):
        snap = RateLimitSnapshot(primary=RateLimitWindow(10.0))
        assert snap.has_data() is True

    def test_has_data_false_when_empty(self):
        assert RateLimitSnapshot().has_data() is False

    def test_to_dict_round_trips_nested_structures(self):
        snap = RateLimitSnapshot(
            limit_id="codex",
            primary=RateLimitWindow(10.0, 60, 123),
            credits=CreditsSnapshot(True, False, "5"),
        )
        d = snap.to_dict()
        assert d["primary"] == {
            "used_percent": 10.0,
            "window_minutes": 60,
            "resets_at": 123,
        }
        assert d["credits"] == {
            "has_credits": True,
            "unlimited": False,
            "balance": "5",
        }
        assert d["secondary"] is None

    def test_usage_snapshot_is_empty_when_no_data(self):
        assert UsageSnapshot().is_empty() is True
        assert UsageSnapshot(snapshots=[RateLimitSnapshot()]).is_empty() is True

    def test_usage_snapshot_not_empty_with_promo(self):
        assert UsageSnapshot(promo_message="hi").is_empty() is False


class TestCaptureFromHeaders:
    def test_headers_become_usage_snapshot(self):
        headers = {
            "x-codex-primary-used-percent": "20",
            "x-codex-promo-message": "promo",
        }
        usage = capture_from_headers(headers)
        assert usage.promo_message == "promo"
        assert usage.snapshots[0].primary.used_percent == 20.0


class TestProcessCache:
    def test_set_and_get_cached_round_trip(self):
        clear_cache()
        snap = UsageSnapshot(
            snapshots=[RateLimitSnapshot(primary=RateLimitWindow(5.0))]
        )
        set_cached(snap, now=999.0)
        cached = get_cached()
        assert cached is snap
        assert cached.captured_at == 999.0
        clear_cache()

    def test_empty_snapshot_does_not_overwrite_useful_data(self):
        clear_cache()
        useful = UsageSnapshot(
            snapshots=[RateLimitSnapshot(primary=RateLimitWindow(5.0))]
        )
        set_cached(useful, now=1.0)
        # a later response with no rate-limit data must not clobber the cache
        set_cached(UsageSnapshot(), now=2.0)
        assert get_cached() is useful
        clear_cache()

    def test_clear_cache_resets_to_none(self):
        set_cached(
            UsageSnapshot(snapshots=[RateLimitSnapshot(primary=RateLimitWindow(1.0))])
        )
        clear_cache()
        assert get_cached() is None
