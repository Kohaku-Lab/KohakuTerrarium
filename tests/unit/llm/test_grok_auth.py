"""Behavior tests for read-only Grok subscription credential discovery."""

import json
import time

from kohakuterrarium.llm.grok_auth import (
    GROK_CLI_BASE_URL,
    GROK_CLI_SOURCE,
    OPENCODE_SOURCE,
    GrokTokens,
)


class TestGrokTokens:
    def test_grok_cli_is_preferred_and_secret_repr_is_redacted(
        self, tmp_path, monkeypatch
    ):
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text(
            json.dumps(
                {
                    "issuer/client": {
                        "key": "grok-secret-canary",
                        "refresh_token": "must-not-be-read",
                        "expires_at": time.time() + 3600,
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
                        "type": "oauth",
                        "access": "opencode-secret-canary",
                        "refresh": "must-not-be-read",
                        "expires": int((time.time() + 3600) * 1000),
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))

        candidates = GrokTokens.load_candidates()

        assert [item.source for item in candidates] == [
            GROK_CLI_SOURCE,
            OPENCODE_SOURCE,
        ]
        assert candidates[0].extra_headers == {"X-XAI-Token-Auth": "xai-grok-cli"}
        assert candidates[0].base_url == GROK_CLI_BASE_URL
        assert candidates[0].media_base_url == "https://api.x.ai/v1"
        assert "grok-secret-canary" not in repr(candidates[0])
        assert "must-not-be-read" not in repr(candidates)

    def test_expired_and_malformed_sources_are_skipped(self, tmp_path, monkeypatch):
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text("{bad", encoding="utf-8")
        opencode = tmp_path / "opencode.json"
        opencode.write_text(
            json.dumps(
                {
                    "xai": {
                        "type": "oauth",
                        "access": "expired",
                        "expires": int((time.time() - 60) * 1000),
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))

        assert GrokTokens.load_candidates() == []
        assert GrokTokens.available() is False

    def test_missing_grok_cli_falls_back_to_opencode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "missing"))
        opencode = tmp_path / "opencode.json"
        opencode.write_text(
            json.dumps(
                {
                    "xai": {
                        "type": "oauth",
                        "access": "usable",
                        "expires": int((time.time() + 3600) * 1000),
                    }
                }
            ),
            encoding="utf-8",
        )
        before = opencode.read_bytes()
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))

        candidates = GrokTokens.load_candidates()

        assert [item.source for item in candidates] == [OPENCODE_SOURCE]
        assert opencode.read_bytes() == before

    def test_non_xai_opencode_entry_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "missing"))
        opencode = tmp_path / "opencode.json"
        opencode.write_text(
            json.dumps({"openai": {"type": "oauth", "access": "other"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(opencode))

        assert GrokTokens.load_candidates() == []
