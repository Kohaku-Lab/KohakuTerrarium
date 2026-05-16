"""Unit tests for :mod:`kohakuterrarium.bootstrap.llm`.

These tests target the resolution / extraction logic and error paths.
Actual provider construction (OpenAI / Anthropic / Codex / LiteLLM) is
covered by the 3rd-party-provider exception in the coverage policy
since each requires live API keys.
"""

import pytest

from kohakuterrarium.bootstrap import llm as llm_mod
from kohakuterrarium.bootstrap.llm import (
    _agent_config_default,
    _create_from_inline,
    _create_from_profile,
    _extract_controller_data,
    _is_meaningful_config_value,
    create_llm_provider,
    create_llm_from_profile_name,
)
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.profile_types import LLMProfile

# ── _agent_config_default ───────────────────────────────────────


class TestAgentConfigDefault:
    def test_field_with_default(self):
        # ``temperature`` has a default of 0.7.
        assert _agent_config_default("temperature") == 0.7

    def test_field_with_default_factory(self):
        # ``extra_body`` has a dict default_factory.
        assert _agent_config_default("extra_body") == {}


# ── _is_meaningful_config_value ─────────────────────────────────


class TestIsMeaningfulConfigValue:
    def test_none_not_meaningful(self):
        assert _is_meaningful_config_value("model", None) is False

    def test_empty_str_not_meaningful(self):
        assert _is_meaningful_config_value("model", "") is False

    def test_default_value_not_meaningful(self):
        assert _is_meaningful_config_value("temperature", 0.7) is False

    def test_overridden_value_meaningful(self):
        assert _is_meaningful_config_value("temperature", 0.1) is True

    def test_empty_dict_not_meaningful(self):
        assert _is_meaningful_config_value("extra_body", {}) is False

    def test_non_empty_dict_meaningful(self):
        assert _is_meaningful_config_value("extra_body", {"k": "v"}) is True


# ── _extract_controller_data ────────────────────────────────────


class TestExtractControllerData:
    def test_defaults_filtered_out(self):
        cfg = AgentConfig(name="a")  # all defaults
        data = _extract_controller_data(cfg)
        # No meaningful overrides → mostly empty.
        # ``llm`` only added when llm_profile is set.
        assert "llm" not in data
        assert "temperature" not in data

    def test_explicit_model(self):
        cfg = AgentConfig(name="a", model="gpt-4", temperature=0.1)
        data = _extract_controller_data(cfg)
        assert data["model"] == "gpt-4"
        assert data["temperature"] == 0.1

    def test_llm_profile_carried_through(self):
        cfg = AgentConfig(name="a", llm_profile="openai/gpt-4")
        data = _extract_controller_data(cfg)
        assert data["llm"] == "openai/gpt-4"


# ── create_llm_provider routes ──────────────────────────────────


class TestCreateLLMProviderProfilePath:
    def test_profile_resolution_used_when_available(self, monkeypatch):
        """When profile resolution returns a profile, the profile branch
        is taken and ``_create_from_profile`` runs."""
        captured = {}

        class _FakeProfile:
            name = "test"
            provider = "openai"
            backend_type = "codex"
            model = "test-model"
            reasoning_effort = None
            service_tier = None
            retry_policy = None
            max_context = 8000
            backend_provider_name = "openai"
            backend_native_tools = None

        def fake_resolve(data, override=None):
            captured["data"] = data
            return _FakeProfile()

        monkeypatch.setattr(llm_mod, "resolve_controller_llm", fake_resolve)

        # Avoid actually building the codex provider — patch it.
        class _StubProvider:
            def __init__(self, **kw):
                pass

        monkeypatch.setattr(llm_mod, "CodexOAuthProvider", _StubProvider)
        cfg = AgentConfig(name="a", model="m")
        out = create_llm_provider(cfg)
        assert isinstance(out, _StubProvider)


class TestCreateLLMProviderInlinePath:
    def test_no_model_raises(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "resolve_controller_llm", lambda *a, **kw: None)
        cfg = AgentConfig(name="a")  # no model
        with pytest.raises(ValueError, match="No LLM model"):
            create_llm_provider(cfg)

    def test_inline_no_api_key_raises(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "resolve_controller_llm", lambda *a, **kw: None)
        cfg = AgentConfig(name="a", model="gpt-4", api_key_env="NONEXISTENT_KEY_XYZ")
        # No API key in env → raises.
        with pytest.raises(ValueError, match="API key not found"):
            create_llm_provider(cfg)


class TestCreateLLMFromProfileName:
    def test_unknown_profile_raises(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "resolve_controller_llm", lambda *a, **kw: None)
        with pytest.raises(ValueError, match="Model profile not found"):
            create_llm_from_profile_name("ghost")

    def test_known_profile_builds_provider(self, monkeypatch):
        profile = LLMProfile(
            name="p", model="gpt-4", provider="openai", backend_type="openai"
        )
        monkeypatch.setattr(llm_mod, "resolve_controller_llm", lambda *a, **kw: profile)
        monkeypatch.setattr(llm_mod, "get_api_key", lambda p: "fake-key")
        provider = create_llm_from_profile_name("p")
        assert isinstance(provider, OpenAIProvider)
        assert provider.config.model == "gpt-4"


# ── _create_from_profile: per-backend construction ──────────────


class TestCreateFromProfile:
    def test_openai_backend_builds_openai_provider(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "get_api_key", lambda p: "k")
        profile = LLMProfile(
            name="p",
            model="gpt-4o",
            provider="openai",
            backend_type="openai",
            max_context=123456,
        )
        provider = _create_from_profile(profile)
        assert isinstance(provider, OpenAIProvider)
        # max_context from the profile is stamped onto the provider.
        assert provider._profile_max_context == 123456

    def test_anthropic_backend_builds_anthropic_provider(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "get_api_key", lambda p: "k")
        profile = LLMProfile(
            name="p",
            model="claude-x",
            provider="anthropic",
            backend_type="anthropic",
        )
        provider = _create_from_profile(profile)
        assert isinstance(provider, AnthropicProvider)
        assert provider.config.model == "claude-x"

    def test_litellm_backend_imports_provider_lazily(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "get_api_key", lambda p: "k")

        class _StubLiteLLMProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "kohakuterrarium.llm.litellm_provider":
                return type(
                    "_LiteLLMModule",
                    (),
                    {"LiteLLMProvider": _StubLiteLLMProvider},
                )
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", fake_import)
        profile = LLMProfile(
            name="p", model="gemini/x", provider="vertex", backend_type="litellm"
        )
        provider = _create_from_profile(profile)
        assert isinstance(provider, _StubLiteLLMProvider)
        assert provider.kwargs["model"] == "gemini/x"

    def test_codex_backend_builds_codex_provider(self):
        # Codex uses OAuth, no API key lookup.
        profile = LLMProfile(name="p", model="gpt-5", provider="", backend_type="codex")
        provider = _create_from_profile(profile)
        assert isinstance(provider, CodexOAuthProvider)

    def test_api_key_from_env_fallback(self, monkeypatch):
        # provider lookup misses, api_key_env hits.
        seen = []

        def fake_get_api_key(key):
            seen.append(key)
            return "" if key == "openai" else "env-key"

        monkeypatch.setattr(llm_mod, "get_api_key", fake_get_api_key)
        profile = LLMProfile(
            name="p",
            model="gpt-4",
            provider="openai",
            backend_type="openai",
            api_key_env="MY_KEY_ENV",
        )
        provider = _create_from_profile(profile)
        assert isinstance(provider, OpenAIProvider)
        # Both the provider name and the env-var name were consulted.
        assert seen == ["openai", "MY_KEY_ENV"]

    def test_missing_api_key_raises_with_login_hint(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "get_api_key", lambda key: "")
        monkeypatch.setattr(llm_mod._api_keys, "_resolver", None)
        profile = LLMProfile(
            name="needy", model="gpt-4", provider="openai", backend_type="openai"
        )
        with pytest.raises(ValueError, match="kt login openai"):
            _create_from_profile(profile)

    def test_missing_api_key_worker_mode_raises_identity_hint(self, monkeypatch):
        # When a resolver is installed (worker mode), the error names the
        # host identity store, NOT the generic kt-login hint.
        monkeypatch.setattr(llm_mod, "get_api_key", lambda key: "")
        monkeypatch.setattr(llm_mod._api_keys, "_resolver", lambda *a: None)
        profile = LLMProfile(
            name="worker", model="gpt-4", provider="openai", backend_type="openai"
        )
        with pytest.raises(ValueError, match="worker.*mode"):
            _create_from_profile(profile)


# ── _create_from_inline: backward-compat path ───────────────────


class TestCreateFromInline:
    def test_codex_oauth_inline(self, monkeypatch):
        cfg = AgentConfig(name="a", model="gpt-5", auth_mode="codex-oauth")
        provider = _create_from_inline(cfg)
        assert isinstance(provider, CodexOAuthProvider)

    def test_anthropic_inline(self, monkeypatch):
        cfg = AgentConfig(
            name="a",
            model="claude-x",
            auth_mode="anthropic",
            api_key_env="ANTHROPIC_KEY_X",
        )
        monkeypatch.setenv("ANTHROPIC_KEY_X", "k")
        provider = _create_from_inline(cfg)
        assert isinstance(provider, AnthropicProvider)
        assert provider.config.model == "claude-x"

    def test_openai_inline_default(self, monkeypatch):
        cfg = AgentConfig(name="a", model="gpt-4", api_key_env="OPENAI_KEY_X")
        monkeypatch.setenv("OPENAI_KEY_X", "k")
        provider = _create_from_inline(cfg)
        assert isinstance(provider, OpenAIProvider)
        assert provider.config.model == "gpt-4"


# ── _apply_backend_native_identity ──────────────────────────────


class TestApplyBackendNativeIdentity:
    def test_stamps_backend_name(self):
        from kohakuterrarium.bootstrap.llm import _apply_backend_native_identity

        class _Profile:
            backend_provider_name = "openrouter"
            backend_native_tools = ["image_gen"]

        class _Provider:
            provider_name = ""
            provider_native_tools = frozenset()

        p = _Provider()
        _apply_backend_native_identity(p, _Profile())
        assert p.provider_name == "openrouter"
        assert "image_gen" in p.provider_native_tools

    def test_empty_backend_tools_opts_out(self):
        from kohakuterrarium.bootstrap.llm import _apply_backend_native_identity

        class _Profile:
            backend_provider_name = "x"
            backend_native_tools = []

        class _Provider:
            provider_name = "default"
            provider_native_tools = frozenset(["image_gen"])

        p = _Provider()
        _apply_backend_native_identity(p, _Profile())
        # Explicit empty list opts out of all native tools.
        assert p.provider_native_tools == frozenset()

    def test_none_backend_tools_preserves_class_default(self):
        from kohakuterrarium.bootstrap.llm import _apply_backend_native_identity

        class _Profile:
            backend_provider_name = ""
            backend_native_tools = None

        class _Provider:
            provider_name = "default"
            provider_native_tools = frozenset(["x"])

        p = _Provider()
        _apply_backend_native_identity(p, _Profile())
        # None means "use class defaults" → unchanged.
        assert p.provider_native_tools == frozenset(["x"])
        # provider_name was empty → not overwritten.
        assert p.provider_name == "default"


# ── fake_test backend — provider-resolution path for multi-node tests ─────


class TestFakeTestBackend:
    """The ``fake_test`` backend is the credential-resolution test seam.

    Unlike the ScriptedLLM monkeypatch (which short-circuits the
    factory), this backend is selected by a real profile, builds a
    real :class:`FakeLLMProvider`, and routes through
    ``get_api_key(profile.provider)`` — so a missing api key on the
    host's identity store fails the build the same way every real
    provider does.  That property is what lets the multi-node test
    harness reproduce credential-lookup bugs.
    """

    def test_builds_provider_when_key_present(self, monkeypatch, tmp_path):
        from kohakuterrarium.bootstrap import llm as boot_llm
        from kohakuterrarium.llm.profile_types import LLMProfile
        from kohakuterrarium.testing.fake_llm_provider import FakeLLMProvider

        monkeypatch.setattr(boot_llm, "get_api_key", lambda provider: "sk-real")
        script = tmp_path / "script.json"
        script.write_text('{"script": ["hi from fake"]}', encoding="utf-8")

        profile = LLMProfile(
            name="fake_test/echo",
            model="fake-echo",
            provider="openai",
            backend_type="fake_test",
            extra_body={"script_path": str(script)},
        )
        provider = boot_llm._create_from_profile(profile)

        assert isinstance(provider, FakeLLMProvider)
        assert provider.api_key == "sk-real"
        assert provider.script_path == script
        # max_context plumbed through (read by agent_messages).
        assert provider._profile_max_context == profile.max_context

    def test_missing_key_raises(self, monkeypatch):
        from kohakuterrarium.bootstrap import llm as boot_llm
        from kohakuterrarium.llm.profile_types import LLMProfile

        monkeypatch.setattr(boot_llm, "get_api_key", lambda provider: "")
        profile = LLMProfile(
            name="fake_test/echo",
            model="fake-echo",
            provider="openai",
            backend_type="fake_test",
        )
        import pytest

        with pytest.raises(ValueError, match="API key not found"):
            boot_llm._create_from_profile(profile)

    async def test_fake_provider_streams_scripted_text(self, tmp_path):
        from kohakuterrarium.testing.fake_llm_provider import FakeLLMProvider

        script = tmp_path / "s.json"
        script.write_text('{"script": ["first turn", "second turn"]}', encoding="utf-8")
        provider = FakeLLMProvider(api_key="k", script_path=str(script))
        # First turn — yields chunks summing to "first turn".
        chunks_a = []
        async for c in provider.chat([]):
            chunks_a.append(c)
        assert "".join(chunks_a) == "first turn"
        # Second turn — next script entry.
        chunks_b = []
        async for c in provider.chat([]):
            chunks_b.append(c)
        assert "".join(chunks_b) == "second turn"

    async def test_fake_provider_default_when_no_script(self):
        from kohakuterrarium.testing.fake_llm_provider import FakeLLMProvider

        provider = FakeLLMProvider(api_key="k")
        chunks = []
        async for c in provider.chat([]):
            chunks.append(c)
        assert "".join(chunks) == "OK"


# ── deferred-provider path on missing key ────────────────────────


class TestDeferredOnMissingKey:
    """Creature creation must succeed even when no API key is configured.

    Model selection is a runtime concern (user picks via Studio UI or
    ``switch_model``).  Gating creation on a working provider locks
    the user out of an existing conversation just because the host
    hasn't been ``kt login``-ed for that provider yet.  The agent
    build catches the construction error and substitutes a
    :class:`DeferredLLMProvider` whose chat() raises only when the
    user actually drives a turn.
    """

    async def test_deferred_provider_chat_raises_runtime_error(self, tmp_path):
        from kohakuterrarium.llm.deferred_provider import DeferredLLMProvider

        provider = DeferredLLMProvider(reason="API key not found")
        agen = provider.chat([])
        import pytest

        with pytest.raises(RuntimeError, match="no usable LLM provider"):
            async for _ in agen:
                pass

    async def test_deferred_provider_chat_complete_raises(self):
        from kohakuterrarium.llm.deferred_provider import DeferredLLMProvider

        provider = DeferredLLMProvider(reason="no profile")
        import pytest

        with pytest.raises(RuntimeError, match="no usable LLM provider"):
            await provider.chat_complete([])

    async def test_agent_init_substitutes_deferred_on_missing_key(
        self, monkeypatch, tmp_path
    ):
        # Drive the agent build with a profile whose key cannot be
        # resolved.  Without the deferred path this raises during
        # creation; with it, the agent boots and its ``llm`` is a
        # :class:`DeferredLLMProvider` whose ``chat`` raises with the
        # original reason.
        from kohakuterrarium.bootstrap import agent_init as ai_mod
        from kohakuterrarium.llm.deferred_provider import DeferredLLMProvider

        def _raise(config, llm_override=None):
            raise ValueError("API key not found for profile 'defprofile'")

        monkeypatch.setattr(ai_mod, "create_llm_provider", _raise)

        # Minimal shape that ``_init_llm`` needs.
        class _Stub:
            _init_llm = ai_mod.AgentInitMixin._init_llm

        stub = _Stub()
        stub.config = type("C", (), {"model": ""})()
        stub._init_llm()
        assert isinstance(stub.llm, DeferredLLMProvider)
        assert "defprofile" in stub.llm.reason
