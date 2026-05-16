"""Unit tests for :mod:`kohakuterrarium.studio.identity.llm_profiles`.

Most functions are thin wrappers over ``kohakuterrarium.llm.profiles``.
We focus on pure helpers and the input-validation logic.
"""

import pytest

from kohakuterrarium.studio.identity.llm_profiles import (
    get_profile_for_identifier,
    save_profile_record,
    split_identifier,
)


class TestSplitIdentifier:
    def test_bare_name(self):
        assert split_identifier("gpt-4o") == ("", "gpt-4o")

    def test_qualified(self):
        assert split_identifier("openai/gpt-4o") == ("openai", "gpt-4o")

    def test_multi_slash(self):
        # Only the first slash splits; rest stays in the name.
        assert split_identifier("a/b/c") == ("a", "b/c")


class TestSaveProfileRecord:
    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("", "model", "provider")

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("name", "", "provider")

    def test_missing_provider_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("name", "model", "")

    def test_unknown_provider_raises(self, monkeypatch):
        from kohakuterrarium.studio.identity import llm_profiles as mod

        monkeypatch.setattr(mod, "load_backends", lambda: {})
        with pytest.raises(ValueError, match="Provider not found"):
            save_profile_record("name", "model", "ghost")


class TestGetProfileForIdentifier:
    def test_delegates_to_split_then_lookup(self, monkeypatch):
        from kohakuterrarium.studio.identity import llm_profiles as mod

        calls = {}

        def fake_get_profile(bare, provider):
            calls["bare"] = bare
            calls["provider"] = provider
            return "found"

        monkeypatch.setattr(mod, "get_profile", fake_get_profile)
        out = get_profile_for_identifier("openai/gpt-4o")
        assert out == "found"
        assert calls == {"bare": "gpt-4o", "provider": "openai"}
