"""Unit tests for credential-only providers in the Rich settings overlay."""

from kohakuterrarium.builtins.cli_rich.dialogs import settings
from kohakuterrarium.builtins.cli_rich.dialogs.settings import SettingsOverlay


class TestSettingsCredentialProviders:
    def test_deepseek_key_is_visible_without_an_llm_backend(self, monkeypatch):
        monkeypatch.setattr(settings, "load_backends", lambda: {})
        monkeypatch.setattr(
            settings,
            "get_api_key",
            lambda provider: "ds-secret" if provider == "deepseek" else "",
        )

        rows = SettingsOverlay()._load_keys()
        deepseek = next(row for row in rows if row["provider"] == "deepseek")

        assert deepseek["has_key"] is True
        assert deepseek["env"] == "DEEPSEEK_API_KEY"
        assert "ds-secret" not in deepseek["masked"]
