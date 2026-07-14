"""Unit tests for :mod:`kohakuterrarium.llm.codex_claims`.

The OIDC id-token decoder must never raise on malformed input — a bad
token yields ``""`` (treated as "unknown account id") rather than an
exception on the usage / consume hot path.
"""

import base64
import json

from kohakuterrarium.llm.codex_claims import account_id_from_id_token


def _fake_id_token(payload: dict) -> str:
    """Build a JWT-shaped string whose middle segment is ``payload``."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


class TestAccountIdFromIdToken:
    def test_extracts_chatgpt_account_id_from_auth_claim(self):
        token = _fake_id_token(
            {
                "email": "user@example.test",
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"},
            }
        )
        assert account_id_from_id_token(token) == "acct-123"

    def test_empty_token_returns_empty(self):
        assert account_id_from_id_token("") == ""

    def test_malformed_token_returns_empty(self):
        # Not a JWT (no payload segment) → empty, never raises.
        assert account_id_from_id_token("not-a-jwt") == ""

    def test_non_base64_payload_returns_empty(self):
        assert account_id_from_id_token("header.!!!not-base64!!!.sig") == ""

    def test_missing_auth_claim_returns_empty(self):
        token = _fake_id_token({"email": "user@example.test"})
        assert account_id_from_id_token(token) == ""

    def test_non_string_account_id_returns_empty(self):
        token = _fake_id_token(
            {"https://api.openai.com/auth": {"chatgpt_account_id": 12345}}
        )
        assert account_id_from_id_token(token) == ""
