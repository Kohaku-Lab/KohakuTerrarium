"""Unit tests for the deterministic helpers in ``llm/codex_auth.py``.

Behavior-first: assert the token expiry math, the on-disk save/load
round-trip for BOTH supported shapes (our flat shape + the Codex CLI
nested shape), the PKCE verifier/challenge relationship, the auth-URL
parameter set, and the headless-environment detection. The OAuth device
/ browser flows that require a live ``auth.openai.com`` endpoint are
NOT exercised here (see the carve-out justification in temp/BUGS.md).
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

from kohakuterrarium.llm.codex_auth import (
    CLIENT_ID,
    REDIRECT_URI,
    CodexTokens,
    _build_auth_url,
    _generate_pkce,
    _is_headless,
)


class TestCodexTokensExpiry:
    def test_token_in_the_future_is_not_expired(self):
        tokens = CodexTokens(
            access_token="a", refresh_token="r", expires_at=time.time() + 3600
        )
        assert tokens.is_expired() is False

    def test_token_in_the_past_is_expired(self):
        tokens = CodexTokens(
            access_token="a", refresh_token="r", expires_at=time.time() - 10
        )
        assert tokens.is_expired() is True

    def test_sixty_second_safety_buffer_applied(self):
        # expires in 30s — within the 60s buffer, so treated as expired
        tokens = CodexTokens(
            access_token="a", refresh_token="r", expires_at=time.time() + 30
        )
        assert tokens.is_expired() is True


class TestCodexTokensPersistence:
    def test_save_then_load_round_trip(self, tmp_path):
        path = tmp_path / "codex-auth.json"
        original = CodexTokens(
            access_token="acc",
            refresh_token="ref",
            expires_at=12345.0,
            id_token="idt",
            account_id="acct",
        )
        original.save(path)
        loaded = CodexTokens.load(path)
        assert loaded == original

    def test_load_missing_file_returns_none(self, tmp_path):
        assert CodexTokens.load(tmp_path / "nope.json") is None

    def test_load_codex_cli_nested_shape(self, tmp_path):
        path = tmp_path / "auth.json"
        path.write_text(
            json.dumps(
                {
                    "tokens": {
                        "access_token": "cli-acc",
                        "refresh_token": "cli-ref",
                        "id_token": "cli-id",
                        "account_id": "cli-acct",
                    },
                    "last_refresh": "2026-05-14T00:00:00+00:00",
                }
            )
        )
        loaded = CodexTokens.load(path)
        assert loaded.access_token == "cli-acc"
        assert loaded.account_id == "cli-acct"
        # last_refresh + ~1h window → a positive expires_at
        assert loaded.expires_at > 0

    def test_load_nested_shape_with_bad_last_refresh_zeroes_expiry(self, tmp_path):
        path = tmp_path / "auth.json"
        path.write_text(
            json.dumps({"tokens": {"access_token": "a"}, "last_refresh": "not-a-date"})
        )
        loaded = CodexTokens.load(path)
        assert loaded.expires_at == 0.0

    def test_load_skips_token_file_with_empty_access_token(self, tmp_path):
        path = tmp_path / "auth.json"
        path.write_text(json.dumps({"access_token": "", "refresh_token": "r"}))
        # no usable access token → load returns None
        assert CodexTokens.load(path) is None

    def test_save_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "codex-auth.json"
        CodexTokens(access_token="a", refresh_token="r").save(path)
        assert path.exists()


class TestGeneratePKCE:
    def test_challenge_is_sha256_of_verifier(self):
        verifier, challenge = _generate_pkce()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

    def test_each_call_produces_a_fresh_verifier(self):
        v1, _ = _generate_pkce()
        v2, _ = _generate_pkce()
        assert v1 != v2


class TestBuildAuthURL:
    def test_auth_url_carries_pkce_and_client_params(self):
        url = _build_auth_url("challenge123", "state456")
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert qs["client_id"] == [CLIENT_ID]
        assert qs["code_challenge"] == ["challenge123"]
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["state"] == ["state456"]
        assert qs["redirect_uri"] == [REDIRECT_URI]
        assert qs["response_type"] == ["code"]


class TestIsHeadless:
    def test_ssh_client_env_means_headless(self, monkeypatch):
        monkeypatch.setenv("SSH_CLIENT", "1.2.3.4 5678 22")
        assert _is_headless() is True

    def test_ssh_tty_env_means_headless(self, monkeypatch):
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
        assert _is_headless() is True

    def test_display_present_means_not_headless(self, monkeypatch):
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        assert _is_headless() is False


# ── codex resolver hook (worker mode) ───────────────────────────────


class TestCodexResolverHook:
    def test_resolver_overrides_local_file(self, tmp_path, monkeypatch):
        from kohakuterrarium.llm.codex_auth import (
            CodexTokens,
            clear_codex_resolver,
            register_codex_resolver,
        )

        # Plant a local file the resolver should HIDE.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        CodexTokens(
            access_token="local-at",
            refresh_token="local-rt",
            expires_at=9999999999,
            id_token="",
            account_id="",
        ).save()

        try:
            register_codex_resolver(
                lambda: CodexTokens(
                    access_token="remote-at",
                    refresh_token="remote-rt",
                    expires_at=9999999999,
                    id_token="",
                    account_id="",
                )
            )
            out = CodexTokens.load()
            assert out is not None
            assert (
                out.access_token == "remote-at"
            ), "resolver result must override the local file in worker mode"
        finally:
            clear_codex_resolver()

    def test_resolver_miss_returns_none_not_local(self, tmp_path, monkeypatch):
        # Worker-mode miss: the resolver returns None; load() must NOT
        # silently fall back to the worker-local file (host-canonical
        # by design — same rule as the api-key resolver).
        from kohakuterrarium.llm.codex_auth import (
            CodexTokens,
            clear_codex_resolver,
            register_codex_resolver,
        )

        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        CodexTokens(
            access_token="local-at",
            refresh_token="local-rt",
            expires_at=9999999999,
            id_token="",
            account_id="",
        ).save()
        try:
            register_codex_resolver(lambda: None)
            assert CodexTokens.load() is None
        finally:
            clear_codex_resolver()

    def test_explicit_path_skips_resolver(self, tmp_path, monkeypatch):
        # When caller passes an explicit path, the resolver is bypassed
        # (used by ``kt login codex`` to write/read a specific file).
        from kohakuterrarium.llm.codex_auth import (
            CodexTokens,
            clear_codex_resolver,
            register_codex_resolver,
        )

        target = tmp_path / "explicit.json"
        CodexTokens(
            access_token="from-explicit",
            refresh_token="",
            expires_at=9999999999,
            id_token="",
            account_id="",
        ).save(target)
        try:
            register_codex_resolver(
                lambda: CodexTokens(
                    access_token="from-resolver",
                    refresh_token="",
                    expires_at=9999999999,
                    id_token="",
                    account_id="",
                )
            )
            out = CodexTokens.load(target)
            assert out is not None
            assert out.access_token == "from-explicit"
        finally:
            clear_codex_resolver()
