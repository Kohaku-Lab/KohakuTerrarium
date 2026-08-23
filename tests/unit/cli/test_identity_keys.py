"""Tests for CLI API-key prompting and cancellation."""

import builtins
import getpass

import pytest

from kohakuterrarium.cli import identity_keys


class TestSecretKeyPrompts:
    def test_set_uses_hidden_prompt(self, monkeypatch, capsys) -> None:
        saved = []
        prompts = []
        monkeypatch.setattr(
            builtins,
            "input",
            lambda _prompt: pytest.fail("secret prompt must not use input()"),
        )
        monkeypatch.setattr(
            getpass,
            "getpass",
            lambda prompt: prompts.append(prompt) or "secret-key",
        )
        monkeypatch.setattr(
            identity_keys,
            "set_key",
            lambda provider, key: saved.append((provider, key)),
        )

        assert identity_keys.set_cli("openai", None) == 0

        assert prompts == ["API key for openai: "]
        assert saved == [("openai", "secret-key")]
        assert "Saved key for: openai" in capsys.readouterr().out

    @pytest.mark.parametrize("exc_type", [EOFError, KeyboardInterrupt])
    def test_set_cancels_cleanly_when_hidden_prompt_stops(
        self, monkeypatch, capsys, exc_type
    ) -> None:
        def cancelled(_prompt):
            raise exc_type

        monkeypatch.setattr(getpass, "getpass", cancelled)
        monkeypatch.setattr(
            identity_keys,
            "set_key",
            lambda *_args: pytest.fail("cancelled prompt must not save a key"),
        )

        assert identity_keys.set_cli("openai", None) == 0
        assert "Cancelled" in capsys.readouterr().out

    def test_login_uses_hidden_prompt(self, monkeypatch) -> None:
        saved = []
        monkeypatch.setattr(identity_keys, "get_existing_key", lambda _provider: None)
        monkeypatch.setattr(
            builtins,
            "input",
            lambda _prompt: pytest.fail("secret prompt must not use input()"),
        )
        monkeypatch.setattr(getpass, "getpass", lambda _prompt: "login-secret")
        monkeypatch.setattr(
            identity_keys,
            "set_key",
            lambda provider, key: saved.append((provider, key)),
        )

        assert identity_keys.login_with_api_key("openai", "OPENAI_API_KEY") == 0
        assert saved == [("openai", "login-secret")]

    @pytest.mark.parametrize("exc_type", [EOFError, KeyboardInterrupt])
    def test_login_cancels_cleanly_when_hidden_prompt_stops(
        self, monkeypatch, capsys, exc_type
    ) -> None:
        def cancelled(_prompt):
            raise exc_type

        monkeypatch.setattr(identity_keys, "get_existing_key", lambda _provider: None)
        monkeypatch.setattr(getpass, "getpass", cancelled)
        monkeypatch.setattr(
            identity_keys,
            "set_key",
            lambda *_args: pytest.fail("cancelled prompt must not save a key"),
        )

        assert identity_keys.login_with_api_key("openai", "OPENAI_API_KEY") == 0
        assert "Cancelled" in capsys.readouterr().out
