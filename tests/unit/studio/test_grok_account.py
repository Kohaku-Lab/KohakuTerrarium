"""Behavior tests for Grok CLI billing normalization and live fetch."""

import asyncio
import json
import math
import time
from datetime import datetime, timezone

import httpx
import pytest

from kohakuterrarium.llm import grok_auth
from kohakuterrarium.llm.grok_auth import GROK_CLI_SOURCE, OPENCODE_SOURCE, GrokToken
from kohakuterrarium.studio.identity import grok_account

LIVE_FIXTURE = {
    "config": {
        "creditUsagePercent": 1.0,
        "prepaidBalance": {"val": 0},
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 0},
        "billingPeriodStart": "2026-09-21T15:08:17.982351+00:00",
        "billingPeriodEnd": "2026-09-28T15:08:17.982351+00:00",
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-09-21T15:08:17.982351+00:00",
            "end": "2026-09-28T15:08:17.982351+00:00",
        },
        "isUnifiedBillingUser": True,
        "productUsage": [{"product": "GrokBuild", "usagePercent": 1.0}],
    }
}

END_EPOCH = datetime.fromisoformat("2026-09-28T15:08:17.982351+00:00").timestamp()


def _token(source=GROK_CLI_SOURCE, access="access-canary", expires_at=9e12):
    headers = (
        {"X-XAI-Token-Auth": "xai-grok-cli", "x-grok-client-version": "1.2.3"}
        if source == GROK_CLI_SOURCE
        else {}
    )
    return GrokToken(
        access_token=access,
        source=source,
        expires_at=expires_at,
        extra_headers=headers,
    )


class TestParseBilling:
    def test_live_fixture_normalizes_weekly_window_and_raw_balance(self):
        parsed = grok_account.parse_billing(LIVE_FIXTURE)

        assert parsed == {
            "window": {
                "period": "weekly",
                "used_percent": 1.0,
                "resets_at": END_EPOCH,
            },
            "products": [{"name": "GrokBuild", "used_percent": 1.0}],
            "prepaid_balance": 0,
        }

    def test_monthly_period_is_not_relabeled_weekly(self):
        body = {
            "config": {
                "creditUsagePercent": 12.5,
                "prepaidBalance": {"val": 3.25},
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_MONTHLY",
                    "end": "2026-10-01T00:00:00Z",
                },
                "productUsage": [
                    {"product": "Grok", "usagePercent": 140},
                    {"product": "Skipped"},
                ],
            }
        }

        parsed = grok_account.parse_billing(body)

        assert parsed["window"]["period"] == "monthly"
        assert parsed["window"]["used_percent"] == 12.5
        assert (
            parsed["window"]["resets_at"]
            == datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
        )
        assert parsed["products"] == [{"name": "Grok", "used_percent": 140.0}]
        assert parsed["prepaid_balance"] == 3.25

    def test_legacy_monthly_limit_ratio_when_percent_is_absent(self):
        parsed = grok_account.parse_billing(
            {
                "config": {
                    "monthlyLimit": {"val": 100},
                    "used": {"val": 25},
                    "billingPeriodEnd": "2026-10-01T00:00:00Z",
                }
            }
        )

        assert parsed == {
            "window": {
                "period": "monthly",
                "used_percent": 25.0,
                "resets_at": datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp(),
            },
            "products": [],
            "prepaid_balance": None,
        }

    def test_explicit_percent_zero_wins_over_legacy_ratio(self):
        parsed = grok_account.parse_billing(
            {
                "creditUsagePercent": 0,
                "monthlyLimit": {"val": 100},
                "used": {"val": 25},
            }
        )

        assert parsed["window"]["period"] == "unknown"
        assert parsed["window"]["used_percent"] == 0.0

    def test_weekly_metadata_with_invalid_percent_is_not_relabeled_monthly(self):
        assert (
            grok_account.parse_billing(
                {
                    "creditUsagePercent": "bad",
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "end": "2026-09-28T15:08:17Z",
                    },
                    "monthlyLimit": {"val": 100},
                    "used": {"val": 25},
                }
            )
            is None
        )

    @pytest.mark.parametrize(
        "body",
        [
            {"monthlyLimit": {"val": 100}, "used": {"val": "25"}},
            {"monthlyLimit": {"val": 100}, "used": {"val": True}},
            {"monthlyLimit": {"val": 0}, "used": {"val": 0}},
            {"monthlyLimit": {"val": -10}, "used": {"val": 1}},
            {"monthlyLimit": {"val": 100}, "used": {"val": -1}},
            {"monthlyLimit": {"val": 100}},
            {"used": {"val": 25}},
            {"monthlyLimit": {"val": math.inf}, "used": {"val": 1}},
            {"monthlyLimit": {"val": 1e-300}, "used": {"val": 1e300}},
            {
                "creditUsagePercent": None,
                "monthlyLimit": {"val": 100},
                "used": {"val": 25},
            },
        ],
    )
    def test_partial_or_invalid_legacy_ratio_is_no_data(self, body):
        assert grok_account.parse_billing(body) is None

    def test_unknown_period_and_naive_iso_are_safe(self):
        parsed = grok_account.parse_billing(
            {
                "creditUsagePercent": 4,
                "prepaidBalance": 8,
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_CUSTOM",
                    "end": "2026-09-28T15:08:17",
                },
                "productUsage": [{"product": "  ", "usagePercent": 1}],
            }
        )

        assert parsed["window"]["period"] == "unknown"
        assert (
            parsed["window"]["resets_at"]
            == datetime(2026, 9, 28, 15, 8, 17, tzinfo=timezone.utc).timestamp()
        )
        assert parsed["products"] == []
        assert parsed["prepaid_balance"] == 8

    def test_huge_int_does_not_raise(self):
        assert grok_account.parse_billing({"creditUsagePercent": 10**10000}) is None

    def test_negative_usage_and_credits_are_rejected(self):
        assert grok_account.parse_billing({"creditUsagePercent": -1}) is None
        assert (
            grok_account.parse_billing(
                {"creditUsagePercent": 1, "prepaidBalance": -0.5}
            )
            is None
        )
        assert (
            grok_account.parse_billing(
                {"creditUsagePercent": 1, "prepaidBalance": {"val": -1}}
            )
            is None
        )

    def test_invalid_optional_balance_does_not_erase_valid_quota(self):
        parsed = grok_account.parse_billing(
            {
                "creditUsagePercent": 7,
                "prepaidBalance": {"note": "unparsed"},
                "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY"},
            }
        )

        assert parsed["window"]["used_percent"] == 7.0
        assert parsed["window"]["period"] == "weekly"
        assert parsed["prepaid_balance"] is None

    def test_result_wrapper_config_is_unwrapped(self):
        parsed = grok_account.parse_billing(
            {"result": {"config": LIVE_FIXTURE["config"]}}
        )

        assert parsed["window"]["period"] == "weekly"
        assert parsed["window"]["used_percent"] == 1.0
        assert parsed["prepaid_balance"] == 0

    @pytest.mark.parametrize(
        "body",
        [
            None,
            [],
            "not-json-object",
            {"config": {"prepaidBalance": {"val": 1}}},
            {
                "creditUsagePercent": True,
                "currentPeriod": {"end": "2026-01-01T00:00:00Z"},
            },
            {"creditUsagePercent": None},
            {"creditUsagePercent": "1"},
            {"creditUsagePercent": math.nan},
            {"creditUsagePercent": math.inf},
            {"creditUsagePercent": 1, "currentPeriod": {"end": "not-a-date"}},
            {"creditUsagePercent": 1, "prepaidBalance": {"val": True}},
            {"creditUsagePercent": 1, "prepaidBalance": {"val": None}},
        ],
    )
    def test_invalid_body_is_no_data(self, body):
        assert grok_account.parse_billing(body) is None

    def test_missing_usage_is_not_coerced_to_zero(self):
        assert (
            grok_account.parse_billing(
                {"prepaidBalance": {"val": 0}, "productUsage": []}
            )
            is None
        )

    def test_bool_product_percent_is_skipped_without_zero(self):
        parsed = grok_account.parse_billing(
            {
                "creditUsagePercent": 2,
                "productUsage": [
                    {"product": "Bad", "usagePercent": True},
                    {"product": "Good", "usagePercent": 0},
                ],
            }
        )
        assert parsed["products"] == [{"name": "Good", "used_percent": 0.0}]
        assert parsed["prepaid_balance"] is None


class TestFetchBilling:
    async def test_sends_bearer_and_owner_headers_to_fixed_endpoint(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["token_auth"] = request.headers.get("x-xai-token-auth")
            seen["version"] = request.headers.get("x-grok-client-version")
            seen["accept"] = request.headers.get("accept")
            return httpx.Response(200, json=LIVE_FIXTURE)

        parsed = await grok_account.fetch_billing(
            _token(), transport=httpx.MockTransport(handler)
        )

        assert seen == {
            "url": "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
            "auth": "Bearer access-canary",
            "token_auth": "xai-grok-cli",
            "version": "1.2.3",
            "accept": "application/json",
        }
        assert parsed["window"]["period"] == "weekly"
        assert parsed["prepaid_balance"] == 0

    async def test_redirects_are_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "https://evil.example/steal"}
            )

        with pytest.raises(grok_account.GrokBillingError) as exc:
            await grok_account.fetch_billing(
                _token(), transport=httpx.MockTransport(handler)
            )
        assert exc.value.kind == "unavailable"
        assert "evil.example" not in str(exc.value)
        assert "access-canary" not in str(exc.value)

    async def test_http_401_and_403_are_auth_failures(self):
        async def status(code):
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    code, json={"error": "secret-body", "token": "access-canary"}
                )
            )
            with pytest.raises(grok_account.GrokBillingError) as exc:
                await grok_account.fetch_billing(_token(), transport=transport)
            assert exc.value.kind == "auth"
            assert exc.value.status_code == code
            assert "secret-body" not in str(exc.value)
            assert "access-canary" not in str(exc.value)

        await status(401)
        await status(403)

    async def test_timeout_and_transport_errors_are_unavailable(self):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("upstream detail access-canary", request=request)

        with pytest.raises(grok_account.GrokBillingError) as exc:
            await grok_account.fetch_billing(
                _token(), transport=httpx.MockTransport(timeout), timeout=0.01
            )
        assert exc.value.kind == "unavailable"
        assert "access-canary" not in str(exc.value)

    async def test_malformed_json_is_no_data(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"{not-json access-canary")
        )
        with pytest.raises(grok_account.GrokBillingError) as exc:
            await grok_account.fetch_billing(_token(), transport=transport)
        assert exc.value.kind == "no_data"
        assert "access-canary" not in str(exc.value)

    async def test_valid_json_without_usage_is_no_data(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"config": {"prepaidBalance": 1}})
        )
        with pytest.raises(grok_account.GrokBillingError) as exc:
            await grok_account.fetch_billing(_token(), transport=transport)
        assert exc.value.kind == "no_data"


class TestGetUsage:
    @pytest.fixture(autouse=True)
    def isolated_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(tmp_path / "opencode.json"))
        monkeypatch.setattr(grok_auth, "_read_grok_cli_version", lambda: "")

    async def test_no_login(self, monkeypatch):
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: []
        )
        result = await grok_account.get_usage()
        assert result == grok_account.empty_usage("not_logged_in")

    async def test_opencode_only_is_unsupported_and_does_not_call_network(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            grok_account.GrokTokens,
            "load_bootstrap_candidates",
            lambda: [_token(OPENCODE_SOURCE, access="opencode-secret")],
        )

        async def fail_refresh(*, force=False):
            raise AssertionError("refresh must not run for OpenCode-only")

        async def fail_fetch(*args, **kwargs):
            raise AssertionError("OpenCode token must not be sent")

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", fail_refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", fail_fetch)

        result = await grok_account.get_usage()
        assert result["status"] == "unsupported"
        assert result["credential_source"] == OPENCODE_SOURCE
        assert "opencode-secret" not in json.dumps(result)

    async def test_expired_cli_beside_valid_opencode_refreshes_without_sending_opencode(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text(
            json.dumps(
                {
                    "issuer/client": {
                        "key": "cli-expired-canary",
                        "expires_at": datetime.fromtimestamp(now - 60, timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                }
            ),
            encoding="utf-8",
        )
        opencode = tmp_path / "opencode.json"
        opencode.write_text(
            json.dumps(
                {
                    "xai": {
                        "access": "opencode-valid-canary",
                        "expires": int((now + 3600) * 1000),
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))
        monkeypatch.setattr(grok_auth.time, "time", lambda: now)
        monkeypatch.setattr(grok_auth, "_grok_cli_executable", lambda: "/test/grok")
        monkeypatch.setattr(grok_auth, "_read_grok_cli_version", lambda: "")
        sent = []

        async def refresh(*, force=False):
            assert force is False
            return _token(access="cli-refreshed", expires_at=now + 6 * 3600)

        async def fetch(active, **kwargs):
            sent.append(active.access_token)
            assert active.source == GROK_CLI_SOURCE
            return grok_account.parse_billing(LIVE_FIXTURE)

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", fetch)

        result = await grok_account.get_usage()

        assert result["status"] == "ok"
        assert result["credential_source"] == GROK_CLI_SOURCE
        assert sent == ["cli-refreshed"]
        dumped = json.dumps(result)
        assert "opencode-valid-canary" not in dumped
        assert "cli-expired-canary" not in dumped

    async def test_fresh_cli_returns_live_payload(self, monkeypatch):
        token = _token(expires_at=9e12)
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [token]
        )

        async def no_force(*, force=False):
            assert force is False
            return token

        async def fetch(active, **kwargs):
            assert active.access_token == "access-canary"
            return grok_account.parse_billing(LIVE_FIXTURE)

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", no_force)
        monkeypatch.setattr(grok_account, "fetch_billing", fetch)

        result = await grok_account.get_usage()
        assert result["status"] == "ok"
        assert result["source"] == "live"
        assert result["credential_source"] == GROK_CLI_SOURCE
        assert isinstance(result["captured_at"], float)
        assert result["window"]["used_percent"] == 1.0
        assert result["products"] == [{"name": "GrokBuild", "used_percent": 1.0}]
        assert result["prepaid_balance"] == 0
        assert "access-canary" not in json.dumps(result)

    async def test_expired_without_refresh_is_auth_expired(self, monkeypatch):
        expired = _token(expires_at=1)
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [expired]
        )

        async def refresh(*, force=False):
            return None

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        result = await grok_account.get_usage()
        assert result["status"] == "auth_expired"
        assert result["credential_source"] == GROK_CLI_SOURCE
        assert result["window"] is None

    async def test_401_retries_once_with_forced_new_token(self, monkeypatch):
        stale = _token(access="old-token", expires_at=9e12)
        fresh = _token(access="new-token", expires_at=9e12)
        calls = []
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [stale]
        )

        async def refresh(*, force=False):
            calls.append(("refresh", force))
            return fresh if force else stale

        async def fetch(active, **kwargs):
            calls.append(("fetch", active.access_token))
            if active.access_token == "old-token":
                raise grok_account.GrokBillingError("auth", status_code=401)
            return grok_account.parse_billing(LIVE_FIXTURE)

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", fetch)

        result = await grok_account.get_usage()
        assert result["status"] == "ok"
        assert calls == [
            ("refresh", False),
            ("fetch", "old-token"),
            ("refresh", True),
            ("fetch", "new-token"),
        ]

    async def test_401_without_new_token_is_auth_expired(self, monkeypatch):
        stale = _token(expires_at=9e12)
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [stale]
        )

        async def refresh(*, force=False):
            return None if force else stale

        async def fetch(active, **kwargs):
            raise grok_account.GrokBillingError("auth", status_code=401)

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", fetch)
        result = await grok_account.get_usage()
        assert result["status"] == "auth_expired"

    async def test_403_is_auth_expired_without_retry(self, monkeypatch):
        token = _token()
        fetches = []
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [token]
        )

        async def refresh(*, force=False):
            if force:
                raise AssertionError("403 must not force another refresh")
            return token

        async def fetch(active, **kwargs):
            fetches.append(active.access_token)
            raise grok_account.GrokBillingError("auth", status_code=403)

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", fetch)
        result = await grok_account.get_usage()
        assert result["status"] == "auth_expired"
        assert fetches == ["access-canary"]

    async def test_timeout_and_no_data_map_without_leaking(self, monkeypatch):
        token = _token()
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [token]
        )

        async def refresh(*, force=False):
            return token

        async def unavailable(active, **kwargs):
            raise grok_account.GrokBillingError("unavailable")

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", refresh)
        monkeypatch.setattr(grok_account, "fetch_billing", unavailable)
        assert (await grok_account.get_usage())["status"] == "unavailable"

        async def no_data(active, **kwargs):
            raise grok_account.GrokBillingError("no_data")

        monkeypatch.setattr(grok_account, "fetch_billing", no_data)
        result = await grok_account.get_usage()
        assert result == grok_account.empty_usage(
            "no_data", credential_source=GROK_CLI_SOURCE
        )

    async def test_refresh_exception_is_redacted_unavailable(self, monkeypatch):
        token = _token(access="secret-access-canary")
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [token]
        )

        async def boom(*, force=False):
            raise RuntimeError("secret-access-canary leaked from refresh")

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", boom)

        result = await grok_account.get_usage()

        assert result["status"] == "unavailable"
        assert result["credential_source"] == GROK_CLI_SOURCE
        assert "secret-access-canary" not in json.dumps(result)
        assert "leaked" not in json.dumps(result)

    async def test_auth_refresh_error_is_auth_expired(self, monkeypatch):
        token = _token(access="secret-access-canary")
        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", lambda: [token]
        )

        async def expired(*, force=False):
            raise grok_auth.GrokAuthError("auth_expired")

        monkeypatch.setattr(grok_account.GrokTokens, "ensure_fresh_cli", expired)

        result = await grok_account.get_usage()

        assert result["status"] == "auth_expired"
        assert "secret-access-canary" not in json.dumps(result)

    async def test_cli_discovery_load_stays_off_the_event_loop(self, monkeypatch):
        def slow_candidates():
            time.sleep(0.2)
            return []

        monkeypatch.setattr(
            grok_account.GrokTokens, "load_bootstrap_candidates", slow_candidates
        )
        loop = asyncio.get_running_loop()
        gaps = []
        stop = asyncio.Event()

        async def heartbeat():
            previous = loop.time()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = loop.time()
                gaps.append(now - previous)
                previous = now

        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.03)
        result = await grok_account.get_usage()
        await asyncio.sleep(0.03)
        stop.set()
        await pulse

        assert result["status"] == "not_logged_in"
        assert max(gaps) < 0.1
