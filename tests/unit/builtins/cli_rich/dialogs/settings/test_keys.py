"""Unit tests for credential-only providers in the Rich settings overlay."""

from kohakuterrarium.builtins.cli_rich.dialogs.settings import settings
from kohakuterrarium.builtins.cli_rich.dialogs.settings.settings import SettingsOverlay


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


class TestSettingsMcpHeaders:
    def test_editing_a_server_in_the_form_keeps_its_headers(self, monkeypatch):
        server = {
            "name": "remote",
            "transport": "streamable_http",
            "url": "http://h/mcp",
            "headers": {"Authorization": "Bearer t0ken"},
        }
        monkeypatch.setattr(settings, "load_servers", lambda: [server])

        overlay = SettingsOverlay()
        row = next(r for r in overlay._load_mcp() if r["name"] == "remote")
        form = overlay._build_mcp_form(row)

        assert row["headers"] == server["headers"]
        assert form.context["preserved_headers"] == server["headers"]
