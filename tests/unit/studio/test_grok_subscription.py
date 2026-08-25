"""Tests for redacted Grok subscription status."""

from kohakuterrarium.llm.grok_auth import GrokToken
from kohakuterrarium.studio.identity.grok_subscription import get_status


class TestGrokSubscriptionStatus:
    def test_status_contains_sources_but_no_tokens(self, monkeypatch):
        monkeypatch.setattr(
            "kohakuterrarium.studio.identity.grok_subscription."
            "GrokTokens.load_bootstrap_candidates",
            lambda: [
                GrokToken(
                    access_token="secret-canary",
                    source="grok-cli",
                    expires_at=123.0,
                ),
                GrokToken(access_token="fallback", source="opencode"),
            ],
        )

        status = get_status()

        assert status == {
            "authenticated": True,
            "source": "grok-cli",
            "sources": ["grok-cli", "opencode"],
            "expires_at": 123.0,
        }
        assert "secret-canary" not in repr(status)

    def test_missing_status(self, monkeypatch):
        monkeypatch.setattr(
            "kohakuterrarium.studio.identity.grok_subscription."
            "GrokTokens.load_bootstrap_candidates",
            lambda: [],
        )
        assert get_status() == {
            "authenticated": False,
            "source": None,
            "sources": [],
        }
