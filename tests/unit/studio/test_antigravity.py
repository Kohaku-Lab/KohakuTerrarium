"""Quota normalization and fixed-destination account reads."""

import json
import math
import time

import httpx
import pytest

from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.studio.identity import antigravity


def summary(fraction=0.75):
    return {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "window": "weekly",
                        "remainingFraction": fraction,
                        "resetTime": "2026-09-30T00:00:00Z",
                    },
                    {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0},
                ],
            },
            {
                "displayName": "Claude and GPT models",
                "buckets": [
                    {
                        "bucketId": "3p-weekly",
                        "window": "weekly",
                        "remainingFraction": 1,
                    },
                ],
            },
        ]
    }


def test_groups_preserve_shared_pool_zero_and_explicit_windows():
    groups = antigravity.parse_quota_summary(summary())
    assert [g["id"] for g in groups] == ["gemini", "third_party"]
    assert groups[0]["windows"] == [
        {"id": "gemini-5h", "period": "5h", "used_percent": 100, "resets_at": None},
        {
            "id": "gemini-weekly",
            "period": "weekly",
            "used_percent": 25,
            "resets_at": 1790726400,
        },
    ]
    assert groups[1]["windows"][0]["used_percent"] == 0


@pytest.mark.parametrize("fraction", [None, True, "0.8", math.nan, math.inf, -0.1, 1.1])
def test_invalid_fraction_stays_unknown(fraction):
    groups = antigravity.parse_quota_summary(summary(fraction))
    assert groups[0]["windows"][1]["used_percent"] is None


def test_unknown_period_is_not_inferred_and_disabled_buckets_are_hidden():
    data = {
        "buckets": [
            {
                "bucketId": "gemini-new",
                "window": "new-window",
                "remainingFraction": 0.5,
                "resetTime": "2026-09-30T00:00:00Z",
            },
            {"bucketId": "3p-weekly", "window": "weekly", "disabled": True},
            None,
        ]
    }
    groups = antigravity.parse_quota_summary(data)
    assert len(groups) == 1
    assert groups[0]["windows"][0]["period"] == "unknown"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"groups": [None, {}, {"buckets": None}]},
        {"buckets": [None, {}, {"bucketId": 123}]},
    ],
)
def test_malformed_or_empty_summary_has_no_data(data):
    assert antigravity.parse_quota_summary(data) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [
        (200, "ok"),
        (401, "auth_expired"),
        (403, "auth_expired"),
        (302, "unavailable"),
        (500, "unavailable"),
        (404, "unavailable"),
    ],
)
async def test_live_request_redaction_and_failures(monkeypatch, code, status):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("secret-canary", time.time() + 3600, "test")],
    )
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.headers["authorization"] == "Bearer secret-canary"
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx.Response(
                200, json={"cloudaicompanionProject": "private-project"}
            )
        assert request.url.path.endswith(":retrieveUserQuotaSummary")
        assert json.loads(request.content) == {"project": "private-project"}
        return httpx.Response(
            code, json=summary(), headers={"location": "https://example.org"}
        )

    result = await antigravity.get_usage(transport=httpx.MockTransport(handle))
    assert result["status"] == status
    assert len(requests) == 2
    assert "secret-canary" not in json.dumps(result)
    assert "private-project" not in json.dumps(result)
    assert bool(result["groups"]) == (status == "ok")
    assert (result["captured_at"] is not None) == (status == "ok")


@pytest.mark.asyncio
async def test_missing_login_does_not_make_a_network_request(monkeypatch):
    monkeypatch.setattr(auth, "read_sources", lambda: [])

    def unexpected(request):
        pytest.fail("Missing login must not reach network")

    assert (await antigravity.get_usage(transport=httpx.MockTransport(unexpected)))[
        "status"
    ] == "not_logged_in"


@pytest.mark.asyncio
async def test_changed_account_drops_quota(monkeypatch):
    tokens = [auth.BorrowedCredential("old", time.time() + 3600, "test")]
    monkeypatch.setattr(auth, "read_sources", lambda: tokens)

    def handle(request):
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "old-project"})
        tokens[:] = [auth.BorrowedCredential("new", time.time() + 3600, "test")]
        return httpx.Response(200, json=summary())

    result = await antigravity.get_usage(transport=httpx.MockTransport(handle))
    assert result["status"] == "auth_expired"
    assert result["groups"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [b"not-json", b"[]", b"{}", b"x" * 4_194_305],
    ids=["invalid-json", "array", "empty", "oversized"],
)
async def test_unusable_response_has_no_quota(monkeypatch, body):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("test", time.time() + 3600, "test")],
    )

    def handle(request):
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx.Response(200, json={"cloudaicompanionProject": "project"})
        return httpx.Response(200, content=body)

    result = await antigravity.get_usage(transport=httpx.MockTransport(handle))
    assert result == antigravity.empty_usage("no_data")


@pytest.mark.asyncio
async def test_network_failure_has_no_raw_diagnostic(monkeypatch):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("test", time.time() + 3600, "test")],
    )

    def handle(request):
        raise httpx.ReadTimeout("private upstream diagnostic", request=request)

    result = await antigravity.get_usage(transport=httpx.MockTransport(handle))
    assert result["status"] == "unavailable"
    assert result["groups"] == []
    assert "private" not in json.dumps(result)
