"""Live key-rotation: ``LLMProvider.reload_credentials()``.

The frontend Settings → Providers page updates ``api_keys.yaml`` while
creatures are still running. Without ``reload_credentials``, the
cached ``AsyncOpenAI`` / ``AsyncAnthropic`` clients keep sending the
stale Authorization header until the user restarts the creature or
the server. The tests below pin the rotation contract for both
providers + the base no-op.
"""

import pytest

from kohakuterrarium.llm import api_keys as _api_keys
from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
from kohakuterrarium.llm.base import BaseLLMProvider
from kohakuterrarium.llm.openai import OpenAIProvider


@pytest.fixture
def cfg_home(tmp_path, monkeypatch):
    """Isolate the api_keys.yaml file under tmp."""
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestBaseNoOp:
    def test_base_class_returns_false(self):
        provider = BaseLLMProvider()
        assert provider.reload_credentials() is False


class TestOpenAIProviderReload:
    def test_no_provider_name_is_noop(self, cfg_home):
        # Inline-config providers don't carry provider_name; rotation
        # would have nothing to resolve against.
        _api_keys.save_api_key("openrouter", "sk-or-newkeyfromfrontend-xxxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-oldkey-xxxxxxx", model="x")
        provider.provider_name = ""
        assert provider.reload_credentials() is False
        assert provider._api_key == "sk-or-oldkey-xxxxxxx"

    def test_unchanged_key_returns_false(self, cfg_home):
        _api_keys.save_api_key("openrouter", "sk-or-samekey-xxxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-samekey-xxxxxxxx", model="x")
        provider.provider_name = "openrouter"
        assert provider.reload_credentials() is False

    def test_rotation_rebuilds_client_with_new_key(self, cfg_home):
        _api_keys.save_api_key("openrouter", "sk-or-OLDOLDOLD-xxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-OLDOLDOLD-xxxxxxx", model="x")
        provider.provider_name = "openrouter"
        old_client = provider._client
        _api_keys.save_api_key("openrouter", "sk-or-NEWNEWNEW-yyyyyyy")
        assert provider.reload_credentials() is True
        assert provider._api_key == "sk-or-NEWNEWNEW-yyyyyyy"
        # SDK client got swapped — not just the stored key attribute.
        assert provider._client is not old_client

    def test_resolver_returns_empty_is_noop(self, cfg_home, monkeypatch):
        # If the resolver gives back an empty string (e.g. user
        # deleted the key) we keep the cached client. Removing a key
        # mid-session shouldn't break in-flight conversations.
        _api_keys.save_api_key("openrouter", "sk-or-OLDOLDOLD-xxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-OLDOLDOLD-xxxxxxx", model="x")
        provider.provider_name = "openrouter"
        monkeypatch.setattr("kohakuterrarium.llm.openai.get_api_key", lambda _p: "")
        assert provider.reload_credentials() is False
        assert provider._api_key == "sk-or-OLDOLDOLD-xxxxxxx"


class TestCredentialProviderField:
    """Pins the production boot path — built-in backends leave the
    native-tool ``provider_name`` field empty, so reload has to use the
    ``_credential_provider`` field bootstrap stamps from
    ``profile.provider`` (the backend name) instead.

    Bug: tests that pre-2026-05-20 only set ``provider.provider_name``
    on a freshly-constructed provider got rotation working in unit
    tests, but the production flow never set ``provider_name`` for the
    built-in openrouter/openai/gemini/mimo backends — so the user's
    'change key in Settings → regen' flow silently no-op'd
    ``reload_credentials``.
    """

    def test_credential_provider_drives_lookup_when_provider_name_empty(self, cfg_home):
        _api_keys.save_api_key("openrouter", "sk-or-OLDOLDOLD-xxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-OLDOLDOLD-xxxxxxx", model="x")
        # native-tool compat field left empty (built-in backend default)
        assert provider.provider_name == ""
        # bootstrap stamps the backend name here:
        provider._credential_provider = "openrouter"
        _api_keys.save_api_key("openrouter", "sk-or-NEWNEWNEW-yyyyyyy")
        assert provider.reload_credentials() is True
        assert provider._api_key == "sk-or-NEWNEWNEW-yyyyyyy"

    def test_no_credential_provider_and_no_provider_name_is_noop(self, cfg_home):
        # Pure-inline configs with no profile / no backend name have
        # nothing to look up against — keep the cached client.
        _api_keys.save_api_key("openrouter", "sk-or-NEWNEWNEW-yyyyyyy")
        provider = OpenAIProvider(api_key="sk-or-OLDOLDOLD-xxxxxxx", model="x")
        # both fields empty
        assert provider.reload_credentials() is False
        assert provider._api_key == "sk-or-OLDOLDOLD-xxxxxxx"

    def test_credential_provider_propagates_through_with_model(self, cfg_home):
        _api_keys.save_api_key("openrouter", "sk-or-OLDOLDOLD-xxxxxxx")
        provider = OpenAIProvider(api_key="sk-or-OLDOLDOLD-xxxxxxx", model="x")
        provider._credential_provider = "openrouter"
        sibling = provider.with_model("other-model")
        assert sibling._credential_provider == "openrouter"
        _api_keys.save_api_key("openrouter", "sk-or-NEWNEWNEW-yyyyyyy")
        # The sibling rotates independently — it has its own state but
        # the same lookup key.
        assert sibling.reload_credentials() is True

    def test_anthropic_credential_provider_drives_lookup(self, cfg_home):
        _api_keys.save_api_key("anthropic", "sk-ant-api03-OLDOLDOLD-xxxxxxx")
        provider = AnthropicProvider(
            api_key="sk-ant-api03-OLDOLDOLD-xxxxxxx", model="claude-sonnet-4-5"
        )
        # native-tool compat empty (built-in default)
        assert provider.provider_name == ""
        provider._credential_provider = "anthropic"
        _api_keys.save_api_key("anthropic", "sk-ant-api03-NEWNEWNEW-yyyyyyy")
        assert provider.reload_credentials() is True
        assert provider._api_key == "sk-ant-api03-NEWNEWNEW-yyyyyyy"


class TestBootstrapStampsCredentialProvider:
    """End-to-end: ``bootstrap/llm.py`` MUST set ``_credential_provider``
    so the engine-level rotation fan-out actually rotates. Regression
    test for the bug where built-in profiles had no provider_name set
    AND no credential field set — so reload silently no-op'd.
    """

    def test_builtin_openrouter_profile_stamps_credential_provider(self, cfg_home):
        from kohakuterrarium.bootstrap.llm import _create_from_profile
        from kohakuterrarium.llm.profile_types import LLMProfile

        _api_keys.save_api_key("openrouter", "sk-or-original-xxxxxxx")
        profile = LLMProfile(
            name="anthropic/claude-sonnet-4-5",
            provider="openrouter",
            model="anthropic/claude-sonnet-4-5",
            backend_type="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            # backend_provider_name left empty — that is the prod default
            # for built-in backends
            backend_provider_name="",
        )
        provider = _create_from_profile(profile)
        assert provider._credential_provider == "openrouter"
        # And the rotation chain now works end to end:
        _api_keys.save_api_key("openrouter", "sk-or-rotated-yyyyyyy")
        assert provider.reload_credentials() is True
        assert provider._api_key == "sk-or-rotated-yyyyyyy"


class TestAnthropicProviderReload:
    def test_rotation_rebuilds_anthropic_client(self, cfg_home):
        _api_keys.save_api_key("anthropic", "sk-ant-api03-OLDOLDOLD-xxxxxxx")
        provider = AnthropicProvider(
            api_key="sk-ant-api03-OLDOLDOLD-xxxxxxx", model="claude-sonnet-4-5"
        )
        provider.provider_name = "anthropic"
        old_client = provider._client
        _api_keys.save_api_key("anthropic", "sk-ant-api03-NEWNEWNEW-yyyyyyy")
        assert provider.reload_credentials() is True
        assert provider._api_key == "sk-ant-api03-NEWNEWNEW-yyyyyyy"
        assert provider._client is not old_client

    def test_bearer_route_preserved_on_reload(self, cfg_home):
        # Routes like OpenRouter via the native Anthropic SDK go over
        # the Bearer path (auth_token=, not api_key=). The rebuilt
        # client must keep that wiring.
        _api_keys.save_api_key("openrouter", "sk-or-OLDOLDOLD-xxxxxxx")
        provider = AnthropicProvider(
            api_key="sk-or-OLDOLDOLD-xxxxxxx",
            base_url="https://openrouter.ai/api/v1",
            model="anthropic/claude-sonnet-4-5",
            auth_as_bearer=True,
        )
        provider.provider_name = "openrouter"
        assert provider.auth_as_bearer is True
        _api_keys.save_api_key("openrouter", "sk-or-NEWNEWNEW-yyyyyyy")
        assert provider.reload_credentials() is True
        # Bearer routing survived the rebuild.
        assert provider.auth_as_bearer is True
