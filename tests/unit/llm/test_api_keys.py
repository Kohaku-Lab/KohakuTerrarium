"""Unit tests for ``llm/api_keys.py`` — key storage + resolution order.

Behavior-first: assert store/retrieve round-trips, the documented
resolution order (resolver > file > env), masking, and worker-mode
authoritative-resolver semantics. The keys file is isolated to a
per-test tmp dir via ``KT_CONFIG_DIR`` so real user state is never
touched; the ``_resolver`` global is restored by the suite-wide
``isolate_global_state`` fixture.
"""

import pytest

from kohakuterrarium.llm import api_keys as ak
from kohakuterrarium.llm.api_keys import (
    PROVIDER_KEY_MAP,
    clear_api_key_resolver,
    get_api_key,
    list_api_keys,
    register_api_key_resolver,
    save_api_key,
)


@pytest.fixture
def keys_file(tmp_path, monkeypatch):
    """Isolate the api-keys file via ``KT_CONFIG_DIR``; clear resolver.

    ``api_keys.py`` resolves its path through ``config_dir()`` (honours
    ``KT_CONFIG_DIR``) — redirecting via the env var is the documented
    isolation seam AND keeps the suite from writing the operator's real
    ``~/.kohakuterrarium/api_keys.yaml``.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "api_keys.yaml"
    clear_api_key_resolver()
    # ensure env doesn't bleed into tests
    for env in PROVIDER_KEY_MAP.values():
        monkeypatch.delenv(env, raising=False)
    return path


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_save_then_get_round_trips(self, keys_file):
        save_api_key("openrouter", "sk-or-secret123")
        assert get_api_key("openrouter") == "sk-or-secret123"

    def test_save_creates_the_file(self, keys_file):
        assert not keys_file.exists()
        save_api_key("openai", "sk-abc")
        assert keys_file.exists()

    def test_save_multiple_providers_independent(self, keys_file):
        save_api_key("openai", "sk-openai")
        save_api_key("anthropic", "sk-ant")
        assert get_api_key("openai") == "sk-openai"
        assert get_api_key("anthropic") == "sk-ant"

    def test_save_overwrites_existing_key(self, keys_file):
        save_api_key("gemini", "old")
        save_api_key("gemini", "new")
        assert get_api_key("gemini") == "new"

    def test_get_missing_provider_returns_empty_string(self, keys_file):
        assert get_api_key("openrouter") == ""

    def test_missing_file_loads_as_empty(self, keys_file):
        # no file written yet
        assert ak._load_api_keys() == {}

    def test_corrupt_file_loads_as_empty(self, keys_file):
        keys_file.write_text("this: is: not: valid: yaml: [", encoding="utf-8")
        assert ak._load_api_keys() == {}

    def test_non_dict_yaml_loads_as_empty(self, keys_file):
        keys_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
        assert ak._load_api_keys() == {}


# ---------------------------------------------------------------------------
# Resolution order: file > env
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    def test_stored_file_key_wins_over_env(self, keys_file, monkeypatch):
        save_api_key("openai", "file-key")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        # docstring resolution order: file (2) before env (3)
        assert get_api_key("openai") == "file-key"

    def test_env_used_when_no_file_key(self, keys_file, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-or-key")
        assert get_api_key("openrouter") == "env-or-key"

    def test_empty_file_key_falls_through_to_env(self, keys_file, monkeypatch):
        save_api_key("openai", "")  # stored but empty
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        # docstring: "provider in keys and keys[provider]" — empty value skips
        assert get_api_key("openai") == "env-key"

    def test_lookup_by_env_var_name_normalizes_to_provider(self, keys_file):
        save_api_key("anthropic", "sk-ant-stored")
        # passing the env var name should resolve to the same provider key
        assert get_api_key("ANTHROPIC_API_KEY") == "sk-ant-stored"

    def test_unknown_provider_falls_back_to_same_named_env(
        self, keys_file, monkeypatch
    ):
        # a provider with no PROVIDER_KEY_MAP entry: env var == provider name
        monkeypatch.setenv("MYCUSTOM", "custom-key")
        assert get_api_key("MYCUSTOM") == "custom-key"

    def test_not_found_anywhere_returns_empty(self, keys_file):
        assert get_api_key("nonexistent") == ""


# ---------------------------------------------------------------------------
# Registered resolver (worker mode) — authoritative
# ---------------------------------------------------------------------------


class TestResolver:
    def test_resolver_consulted_first(self, keys_file, monkeypatch):
        save_api_key("openai", "file-key")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        register_api_key_resolver(lambda provider: "resolver-key")
        # docstring step 0: resolver is authoritative when present
        assert get_api_key("openai") == "resolver-key"

    def test_resolver_receives_normalized_provider_name(self, keys_file):
        seen = []
        register_api_key_resolver(lambda provider: seen.append(provider) or "k")
        get_api_key("OPENAI_API_KEY")
        # env var name normalized to provider before the resolver sees it
        assert seen == ["openai"]

    def test_resolver_miss_returns_empty_not_file_fallback(
        self, keys_file, monkeypatch
    ):
        save_api_key("openai", "file-key")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        register_api_key_resolver(lambda provider: "")
        # docstring: worker-mode miss returns "" — never falls through to
        # the worker's own file/env (host-canonical identity design)
        assert get_api_key("openai") == ""

    def test_resolver_exception_treated_as_miss(self, keys_file, monkeypatch):
        save_api_key("openai", "file-key")

        def _boom(provider):
            raise RuntimeError("resolver broke")

        register_api_key_resolver(_boom)
        # exception swallowed -> treated as miss -> "" (no file fallback)
        assert get_api_key("openai") == ""

    def test_clear_resolver_restores_file_path(self, keys_file):
        save_api_key("openai", "file-key")
        register_api_key_resolver(lambda provider: "resolver-key")
        assert get_api_key("openai") == "resolver-key"
        clear_api_key_resolver()
        # after clearing, the standalone file path is back
        assert get_api_key("openai") == "file-key"

    def test_clear_resolver_is_idempotent(self, keys_file):
        clear_api_key_resolver()
        clear_api_key_resolver()  # no error
        assert ak._resolver is None


# ---------------------------------------------------------------------------
# list_api_keys — masking
# ---------------------------------------------------------------------------


class TestListApiKeys:
    def test_long_key_masked_with_prefix_and_suffix(self, keys_file):
        save_api_key("openrouter", "sk-or-1234567890")
        masked = list_api_keys()
        # >8 chars: first 4 + "..." + last 4
        assert masked == {"openrouter": "sk-o...7890"}

    def test_short_key_fully_masked(self, keys_file):
        save_api_key("openai", "shortk")  # len 6 <= 8
        assert list_api_keys() == {"openai": "****"}

    def test_eight_char_key_fully_masked(self, keys_file):
        save_api_key("openai", "12345678")  # len == 8, not > 8
        assert list_api_keys() == {"openai": "****"}

    def test_empty_key_omitted_from_listing(self, keys_file):
        save_api_key("openai", "")
        assert list_api_keys() == {}

    def test_empty_when_no_keys_stored(self, keys_file):
        assert list_api_keys() == {}

    def test_multiple_keys_all_masked(self, keys_file):
        save_api_key("openai", "sk-openai-longkey")
        save_api_key("gemini", "abc")
        masked = list_api_keys()
        assert masked == {"openai": "sk-o...gkey", "gemini": "****"}


# ---------------------------------------------------------------------------
# PROVIDER_KEY_MAP integrity
# ---------------------------------------------------------------------------


class TestProviderKeyMap:
    def test_known_providers_mapped(self):
        assert PROVIDER_KEY_MAP["openai"] == "OPENAI_API_KEY"
        assert PROVIDER_KEY_MAP["anthropic"] == "ANTHROPIC_API_KEY"
        assert PROVIDER_KEY_MAP["openrouter"] == "OPENROUTER_API_KEY"
        assert PROVIDER_KEY_MAP["gemini"] == "GEMINI_API_KEY"
        assert PROVIDER_KEY_MAP["mimo"] == "MIMO_API_KEY"
