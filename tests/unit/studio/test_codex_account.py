"""Unit tests for :mod:`kohakuterrarium.studio.identity.codex_account`.

The live WHAM client is exercised through an ``httpx.MockTransport`` so
no real network / OAuth is touched. We pin the exact request URL +
headers (access-token bearer, NOT the id-token; ``ChatGPT-Account-Id``;
codex user agent), the four consume outcomes, and — critically — that a
usage read / redeem hits ONLY the ``/wham/*`` endpoints (zero model /
Responses calls).
"""

import base64
import json

import httpx
import pytest

from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.studio.identity import codex_account as mod
from kohakuterrarium.studio.identity.codex_account import (
    CONSUME_URL,
    RESET_CREDITS_URL,
    USAGE_URL,
    consume_reset_credit,
    fetch_usage,
    list_reset_credits,
)


def _tokens() -> CodexTokens:
    return CodexTokens(
        access_token="access-XYZ",
        refresh_token="refresh-1",
        expires_at=9999999999,
        id_token="id-token-should-never-be-the-bearer",
        account_id="acct-777",
    )


def _id_token(account_id: str) -> str:
    payload = {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


class _Recorder:
    """MockTransport handler that records requests + serves canned JSON."""

    def __init__(self, routes: dict[str, dict]):
        # routes: url -> {"status": int, "json": obj}
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, json={"error": "no route"})
        return httpx.Response(route["status"], json=route["json"])

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


# ── fetch_usage ─────────────────────────────────────────────────


class TestFetchUsage:
    async def test_hits_wham_usage_with_access_token_bearer(self):
        rec = _Recorder(
            {
                USAGE_URL: {
                    "status": 200,
                    "json": {
                        "plan_type": "pro",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25,
                                "limit_window_seconds": 300,
                                "reset_at": 111,
                            }
                        },
                        "rate_limit_reset_credits": {"available_count": 3},
                    },
                }
            }
        )
        out = await fetch_usage(_tokens(), transport=rec.transport())

        req = rec.requests[0]
        assert req.method == "GET"
        assert str(req.url) == USAGE_URL
        # Bearer MUST be the access token, never the id-token.
        assert req.headers["Authorization"] == "Bearer access-XYZ"
        assert "id-token-should-never-be-the-bearer" not in req.headers["Authorization"]
        assert req.headers["ChatGPT-Account-Id"] == "acct-777"
        assert req.headers["User-Agent"].startswith("codex_cli_rs/")

        assert out["available_count"] == 3
        assert out["snapshots"][0]["limit_id"] == "codex"
        assert out["snapshots"][0]["primary"]["used_percent"] == 25.0

    async def test_missing_account_id_omits_header(self):
        tokens = _tokens()
        tokens.account_id = ""
        tokens.id_token = ""
        rec = _Recorder({USAGE_URL: {"status": 200, "json": {"plan_type": "free"}}})
        await fetch_usage(tokens, transport=rec.transport())
        assert "ChatGPT-Account-Id" not in rec.requests[0].headers

    async def test_account_id_derived_from_id_token_when_absent(self):
        # No stored account_id, but the id-token carries the claim →
        # the header is still populated (never the id-token as bearer).
        tokens = _tokens()
        tokens.account_id = ""
        tokens.id_token = _id_token("acct-derived")
        rec = _Recorder({USAGE_URL: {"status": 200, "json": {"plan_type": "free"}}})
        await fetch_usage(tokens, transport=rec.transport())
        req = rec.requests[0]
        assert req.headers["ChatGPT-Account-Id"] == "acct-derived"
        assert req.headers["Authorization"] == "Bearer access-XYZ"

    async def test_http_error_raises(self):
        rec = _Recorder({USAGE_URL: {"status": 401, "json": {"detail": "nope"}}})
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_usage(_tokens(), transport=rec.transport())

    async def test_no_model_or_responses_calls_on_read(self):
        rec = _Recorder(
            {
                USAGE_URL: {
                    "status": 200,
                    "json": {"rate_limit_reset_credits": {"available_count": 0}},
                },
                RESET_CREDITS_URL: {
                    "status": 200,
                    "json": {"credits": [], "available_count": 0},
                },
            }
        )
        await fetch_usage(_tokens(), transport=rec.transport())
        await list_reset_credits(_tokens(), transport=rec.transport())
        assert rec.urls  # requests actually happened
        for url in rec.urls:
            assert "/wham/" in url
            assert "/responses" not in url
            assert not url.endswith("/codex")
            assert "/backend-api/codex" not in url


# ── list_reset_credits ──────────────────────────────────────────


class TestListResetCredits:
    async def test_parses_credit_details(self):
        rec = _Recorder(
            {
                RESET_CREDITS_URL: {
                    "status": 200,
                    "json": {
                        "available_count": 2,
                        "total_earned_count": 4,
                        "credits": [
                            {
                                "id": "credit-1",
                                "reset_type": "codex_rate_limits",
                                "status": "available",
                                "granted_at": "2026-06-17T00:00:00Z",
                                "expires_at": "2026-07-17T00:00:00Z",
                                "title": "Full reset",
                                "description": "Ready",
                            },
                            {"id": "", "reset_type": "x"},
                        ],
                    },
                }
            }
        )
        out = await list_reset_credits(_tokens(), transport=rec.transport())
        assert str(rec.requests[0].url) == RESET_CREDITS_URL
        assert out["available_count"] == 2
        # The credit with an empty id is dropped.
        assert len(out["credits"]) == 1
        assert out["credits"][0] == {
            "id": "credit-1",
            "reset_type": "codex_rate_limits",
            "status": "available",
            "granted_at": "2026-06-17T00:00:00Z",
            "expires_at": "2026-07-17T00:00:00Z",
            "title": "Full reset",
            "description": "Ready",
        }

    async def test_detail_failure_falls_back_to_count(self):
        rec = _Recorder({RESET_CREDITS_URL: {"status": 500, "json": {"e": 1}}})
        out = await list_reset_credits(
            _tokens(), fallback_count=5, transport=rec.transport()
        )
        # Detail GET failed → count fallback, empty credit list, no raise.
        assert out == {"available_count": 5, "credits": []}


# ── consume_reset_credit ────────────────────────────────────────


class TestConsumeResetCredit:
    async def test_posts_idempotency_key_and_maps_reset(self):
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"code": "reset"}}})
        outcome = await consume_reset_credit(
            _tokens(), "redeem-abc", transport=rec.transport()
        )
        req = rec.requests[0]
        assert req.method == "POST"
        assert str(req.url) == CONSUME_URL
        assert req.headers["Content-Type"] == "application/json"
        assert json.loads(req.content) == {"redeem_request_id": "redeem-abc"}
        assert outcome == "reset"

    async def test_credit_id_included_when_given(self):
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"code": "reset"}}})
        await consume_reset_credit(
            _tokens(), "redeem-abc", credit_id="credit-1", transport=rec.transport()
        )
        assert json.loads(rec.requests[0].content) == {
            "redeem_request_id": "redeem-abc",
            "credit_id": "credit-1",
        }

    @pytest.mark.parametrize(
        "code, outcome",
        [
            ("reset", "reset"),
            ("nothing_to_reset", "nothingToReset"),
            ("no_credit", "noCredit"),
            ("already_redeemed", "alreadyRedeemed"),
        ],
    )
    async def test_four_outcomes(self, code, outcome):
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"code": code}}})
        result = await consume_reset_credit(
            _tokens(), "redeem-x", transport=rec.transport()
        )
        assert result == outcome

    async def test_camelcase_code_also_maps(self):
        # Robust to either wire form of the outcome code.
        rec = _Recorder(
            {CONSUME_URL: {"status": 200, "json": {"code": "alreadyRedeemed"}}}
        )
        result = await consume_reset_credit(
            _tokens(), "redeem-x", transport=rec.transport()
        )
        assert result == "alreadyRedeemed"

    async def test_empty_idempotency_key_rejected(self):
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"code": "reset"}}})
        with pytest.raises(ValueError):
            await consume_reset_credit(_tokens(), "", transport=rec.transport())
        # No request should have been sent.
        assert rec.requests == []

    async def test_explicit_empty_credit_id_rejected(self):
        # codex-rs parity: an explicit empty credit_id is invalid (only
        # None means "redeem any"). Must reject before any request.
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"code": "reset"}}})
        with pytest.raises(ValueError):
            await consume_reset_credit(
                _tokens(), "redeem-x", credit_id="", transport=rec.transport()
            )
        assert rec.requests == []

    async def test_missing_code_raises(self):
        rec = _Recorder({CONSUME_URL: {"status": 200, "json": {"nope": 1}}})
        with pytest.raises(RuntimeError):
            await consume_reset_credit(_tokens(), "redeem-x", transport=rec.transport())

    async def test_user_agent_constant_is_codex_originator(self):
        assert mod.CODEX_USER_AGENT.startswith("codex_cli_rs/")
