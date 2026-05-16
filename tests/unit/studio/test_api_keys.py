"""Unit tests for :mod:`kohakuterrarium.studio.identity.api_keys`."""

import pytest

from kohakuterrarium.studio.identity import api_keys as mod
from kohakuterrarium.studio.identity.api_keys import (
    get_existing_key,
    list_keys_for_cli,
    list_keys_payload,
    remove_key,
    set_key,
)


class _FakeBackend:
    def __init__(self, backend_type="openai", api_key_env="OPENAI_KEY"):
        self.backend_type = backend_type
        self.api_key_env = api_key_env


@pytest.fixture(autouse=True)
def _mock_backend_layer(monkeypatch):
    """Replace the llm-layer functions so tests don't touch real files."""
    state = {
        "backends": {
            "openai": _FakeBackend(),
            "anthropic": _FakeBackend("anthropic", "AKEY"),
        },
        "stored_keys": {"openai": "sk-abc"},
        "masked": {"openai": "sk-...***"},
    }

    def load_backends():
        return state["backends"]

    def get_api_key(provider):
        return state["stored_keys"].get(provider, "")

    def save_api_key(provider, key):
        if key:
            state["stored_keys"][provider] = key
        else:
            state["stored_keys"].pop(provider, None)

    def list_api_keys():
        return state["masked"]

    def is_available(name):
        return name in state["stored_keys"]

    monkeypatch.setattr(mod, "load_backends", load_backends)
    monkeypatch.setattr(mod, "get_api_key", get_api_key)
    monkeypatch.setattr(mod, "save_api_key", save_api_key)
    monkeypatch.setattr(mod, "list_api_keys", list_api_keys)
    monkeypatch.setattr(mod, "_is_available", is_available)
    return state


# ── list_keys_payload ────────────────────────────────────────────


class TestListKeysPayload:
    def test_shape(self):
        out = list_keys_payload()
        providers = [e["provider"] for e in out]
        assert "openai" in providers
        assert "anthropic" in providers
        openai_entry = next(e for e in out if e["provider"] == "openai")
        assert openai_entry["has_key"] is True
        assert openai_entry["masked_key"] == "sk-...***"


# ── list_keys_for_cli ────────────────────────────────────────────


class TestListKeysForCli:
    def test_stored_source(self):
        rows = list_keys_for_cli()
        openai_row = next(r for r in rows if r["provider"] == "openai")
        assert openai_row["source"] == "stored"
        assert openai_row["shown"] == "sk-...***"

    def test_missing_no_env(self, monkeypatch):
        # Anthropic has no stored key and no env var set.
        monkeypatch.delenv("AKEY", raising=False)
        rows = list_keys_for_cli()
        anth_row = next(r for r in rows if r["provider"] == "anthropic")
        assert anth_row["source"] == "missing"

    def test_env_source(self, monkeypatch):
        monkeypatch.setenv("AKEY", "from-env")
        rows = list_keys_for_cli()
        anth_row = next(r for r in rows if r["provider"] == "anthropic")
        assert anth_row["source"] == "env"
        assert "(from env)" in anth_row["shown"]


# ── set_key ──────────────────────────────────────────────────────


class TestSetKey:
    def test_missing_provider(self):
        with pytest.raises(ValueError, match="required"):
            set_key("", "k")

    def test_missing_key(self):
        with pytest.raises(ValueError, match="required"):
            set_key("openai", "")

    def test_unknown_provider(self):
        with pytest.raises(LookupError, match="not found"):
            set_key("ghost", "k")

    def test_success(self, _mock_backend_layer):
        set_key("anthropic", "new-key")
        assert _mock_backend_layer["stored_keys"]["anthropic"] == "new-key"


class TestRemoveKey:
    def test_unknown_provider(self):
        with pytest.raises(LookupError):
            remove_key("ghost")

    def test_success(self, _mock_backend_layer):
        remove_key("openai")
        assert "openai" not in _mock_backend_layer["stored_keys"]


class TestGetExistingKey:
    def test_known(self):
        assert get_existing_key("openai") == "sk-abc"

    def test_missing(self):
        assert get_existing_key("ghost") == ""
