"""Behavior tests for read-only Grok subscription credential discovery."""

import json
import time
from datetime import datetime, timezone

import asyncio
import pytest

from kohakuterrarium.llm.grok_auth import (
    GROK_CLI_BASE_URL,
    GROK_CLI_SOURCE,
    OPENCODE_SOURCE,
    GrokTokens,
)


class TestGrokTokens:
    @staticmethod
    def _write_cli_auth(grok_home, token, expires_at):
        (grok_home / "auth.json").write_text(
            json.dumps(
                {
                    "issuer/client": {
                        "key": token,
                        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                }
            ),
            encoding="utf-8",
        )

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
        (grok_home / ".metadata_version").write_text("1.0.5\n", encoding="utf-8")
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
        assert candidates[0].extra_headers == {
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-shell",
            "x-grok-client-mode": "interactive",
            "x-grok-client-version": "1.0.5",
        }
        assert candidates[0].base_url == GROK_CLI_BASE_URL
        assert candidates[0].media_base_url == "https://api.x.ai/v1"
        assert "grok-secret-canary" not in repr(candidates[0])
        assert "must-not-be-read" not in repr(candidates)

    def test_invalid_grok_cli_version_is_not_used_as_a_header(
        self, tmp_path, monkeypatch
    ):
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        (grok_home / "auth.json").write_text(
            json.dumps({"key": "usable"}), encoding="utf-8"
        )
        (grok_home / ".metadata_version").write_text(
            "1.0.5\r\nx-injected: yes", encoding="utf-8"
        )
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(tmp_path / "missing"))

        candidates = GrokTokens.load_candidates()

        assert len(candidates) == 1
        assert "x-grok-client-version" not in candidates[0].extra_headers

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

    def test_iso_expiry_is_parsed_and_expired_cli_login_can_bootstrap(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        self._write_cli_auth(grok_home, "expired", now - 60)
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(tmp_path / "missing"))
        monkeypatch.setattr("kohakuterrarium.llm.grok_auth.time.time", lambda: now)
        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_auth._grok_cli_executable",
            lambda: "/test/grok",
        )

        assert GrokTokens.load_candidates() == []
        bootstrap = GrokTokens.load_bootstrap_candidates()
        assert len(bootstrap) == 1
        assert bootstrap[0].expires_at == now - 60
        assert GrokTokens.available() is True

    @pytest.mark.asyncio
    async def test_refreshes_cli_token_inside_thirty_minute_window(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        self._write_cli_auth(grok_home, "old", now + 29 * 60)
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setenv("OPENCODE_AUTH_FILE", str(tmp_path / "missing"))
        monkeypatch.setattr("kohakuterrarium.llm.grok_auth.time.time", lambda: now)
        calls = []

        async def fake_models():
            calls.append("models")
            self._write_cli_auth(grok_home, "new", now + 6 * 3600)
            return False  # The auth-file result, not CLI output/status, is trusted.

        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_auth._run_grok_models", fake_models
        )

        refreshed = await GrokTokens.ensure_fresh_cli()

        assert refreshed is not None
        assert refreshed.access_token == "new"
        assert calls == ["models"]

    @pytest.mark.asyncio
    async def test_does_not_refresh_cli_token_outside_thirty_minute_window(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        self._write_cli_auth(grok_home, "current", now + 31 * 60)
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setattr("kohakuterrarium.llm.grok_auth.time.time", lambda: now)

        async def unexpected_models():
            pytest.fail("Grok CLI should not run before the 30-minute window")

        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_auth._run_grok_models", unexpected_models
        )

        token = await GrokTokens.ensure_fresh_cli()

        assert token is not None
        assert token.access_token == "current"

    @pytest.mark.asyncio
    async def test_concurrent_refreshes_share_one_cli_process(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        self._write_cli_auth(grok_home, "old", now + 60)
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setattr("kohakuterrarium.llm.grok_auth.time.time", lambda: now)
        calls = []

        async def fake_models():
            calls.append("models")
            await asyncio.sleep(0)
            self._write_cli_auth(grok_home, "new", now + 6 * 3600)
            return True

        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_auth._run_grok_models", fake_models
        )

        results = await asyncio.gather(
            GrokTokens.ensure_fresh_cli(), GrokTokens.ensure_fresh_cli()
        )

        assert [item.access_token for item in results if item is not None] == [
            "new",
            "new",
        ]
        assert calls == ["models"]

    @pytest.mark.asyncio
    async def test_forced_refresh_rejects_an_unchanged_access_token(
        self, tmp_path, monkeypatch
    ):
        now = 2_000_000_000.0
        grok_home = tmp_path / "grok"
        grok_home.mkdir()
        self._write_cli_auth(grok_home, "same", now + 6 * 3600)
        monkeypatch.setenv("GROK_HOME", str(grok_home))
        monkeypatch.setattr("kohakuterrarium.llm.grok_auth.time.time", lambda: now)

        async def unchanged_models():
            return True

        monkeypatch.setattr(
            "kohakuterrarium.llm.grok_auth._run_grok_models", unchanged_models
        )

        assert await GrokTokens.ensure_fresh_cli(force=True) is None
