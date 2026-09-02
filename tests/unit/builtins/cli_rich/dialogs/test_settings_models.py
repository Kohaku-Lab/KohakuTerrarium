"""Unit tests for the Models tab in the Rich settings overlay."""

from kohakuterrarium.builtins.cli_rich.dialogs import settings
from kohakuterrarium.builtins.cli_rich.dialogs.settings import SettingsOverlay


class TestSettingsModelsTab:
    def _overlay(self, row: dict) -> SettingsOverlay:
        overlay = SettingsOverlay()
        overlay.tab = "Models"
        overlay._entries["Models"] = [row]
        overlay._cursor["Models"] = 0
        return overlay

    def test_activation_persists_the_provider_qualified_identifier(self, monkeypatch):
        # A bare name shared by several providers would read back as the
        # wrong provider's preset; the row carries the one the user picked.
        captured: list[str] = []
        monkeypatch.setattr(settings, "set_default_model", captured.append)
        overlay = self._overlay(
            {
                "name": "gpt-5.5",
                "provider": "openai",
                "available": True,
                "is_default": False,
                "source": "preset",
            }
        )

        overlay._list_activate()

        assert captured == ["openai/gpt-5.5"]
        assert overlay._flash == "Default model set: openai/gpt-5.5"

    def test_activation_of_a_provider_less_row_keeps_the_bare_name(self, monkeypatch):
        captured: list[str] = []
        monkeypatch.setattr(settings, "set_default_model", captured.append)
        overlay = self._overlay(
            {
                "name": "orphan",
                "provider": "",
                "available": True,
                "is_default": False,
                "source": "user",
            }
        )

        overlay._list_activate()

        assert captured == ["orphan"]
        assert overlay._flash == "Default model set: orphan"

    def test_activation_marks_only_the_clicked_provider_row(self):
        # Real write + reload against the per-test tmp ``KT_CONFIG_DIR``.
        # A bare default is read back through the fixed provider
        # preference, so activating the anthropic row used to leave the
        # openrouter row of the same preset flagged as the default.
        overlay = self._overlay(
            {
                "name": "claude-opus-4.8",
                "provider": "anthropic",
                "available": True,
                "is_default": False,
                "source": "preset",
            }
        )

        overlay._list_activate()

        assert [
            (row["provider"], row["name"])
            for row in overlay._entries["Models"]
            if row["is_default"]
        ] == [("anthropic", "claude-opus-4.8")]

    def test_activation_without_a_key_does_not_write(self, monkeypatch):
        captured: list[str] = []
        monkeypatch.setattr(settings, "set_default_model", captured.append)
        overlay = self._overlay(
            {
                "name": "gpt-5.5",
                "provider": "openai",
                "available": False,
                "is_default": False,
                "source": "preset",
            }
        )

        overlay._list_activate()

        assert captured == []
        assert overlay._flash == "gpt-5.5: provider has no key configured"
