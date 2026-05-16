"""Integration test for the ``llm/`` package — the deterministic surface.

This file is the canonical usage example of everything in ``llm/`` that
does NOT need a live network service: the profile / preset / backend
config system, ``api_keys`` storage, the native tool-schema builder, and
the multimodal ``Message`` / ``ContentPart`` types.

The live provider clients (``openai.py``, ``anthropic_provider.py``,
``codex_*.py``, ``litellm_provider.py``) are CARVED OUT — they require
real endpoints. What is exercised here is the abstraction surface that
``bootstrap/llm.py`` drives before it ever constructs a provider:

    resolve_controller_llm()  -> LLMProfile
    get_api_key()             -> str
    build_tool_schemas()      -> list[ToolSchema]
    Message / ContentPart     -> wire-format dicts

Each test method runs ONE complete workflow end-to-end. No granular
per-method probes, no shape asserts — every assertion pins an exact
resolved value, an exact schema, or an exact round-trip.

Why these collaborators are real:
  * ``LLMBackend`` / ``LLMPreset`` / ``LLMProfile`` — plain dataclasses.
  * The YAML preset store — redirected to ``tmp_path`` via the
    ``PROFILES_PATH`` / ``KEYS_PATH`` module constants, exactly how the
    existing unit suite and ``studio`` tests redirect it.
  * ``api_keys`` storage — same redirect, real file I/O.
  * The builtin preset catalogue — its package-scan + cache globals are
    reset so resolution is deterministic with no installed packages.
"""

from typing import Any

import pytest

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm import api_keys as ak
from kohakuterrarium.llm import backends as backends_mod
from kohakuterrarium.llm import presets as presets_mod
from kohakuterrarium.llm.backends import (
    _normalize_backend_type,
    legacy_provider_from_data,
    validate_backend_type,
)
from kohakuterrarium.llm.base import (
    BaseLLMProvider,
    ChatResponse,
    LLMConfig,
    NativeToolCall,
    ToolSchema,
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.message import (
    AssistantMessage,
    FilePart,
    ImagePart,
    Message,
    SystemMessage,
    TextPart,
    ToolMessage,
    UserMessage,
    create_message,
    dicts_to_messages,
    make_multimodal_content,
    messages_to_dicts,
)
from kohakuterrarium.llm.presets import iter_all_presets, resolve_alias
from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset, LLMProfile
from kohakuterrarium.llm.profiles import (
    delete_backend,
    delete_profile,
    get_api_key,
    get_preset,
    get_profile,
    list_all,
    load_profiles,
    profile_to_identifier,
    resolve_controller_llm,
    save_backend,
    save_profile,
    set_default_model,
)
from kohakuterrarium.llm.tools import build_provider_native_tools, build_tool_schemas
from kohakuterrarium.llm.variations import (
    apply_patch_map,
    apply_variation_groups,
    deep_merge_dicts,
    normalize_variation_selections,
    parse_variation_selector,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Fixtures — redirect every llm/ config file to a per-test tmp dir.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_llm_store(tmp_path, monkeypatch):
    """Isolate ``llm_profiles.yaml`` + ``api_keys.yaml`` via ``KT_CONFIG_DIR``.

    Both ``llm/backends.py`` and ``llm/api_keys.py`` resolve their file
    paths through ``utils.config_dir.config_dir`` — ``KT_CONFIG_DIR``
    is the single isolation seam (and keeps the suite from writing the
    operator's real ``~/.kohakuterrarium/``).  Tests that write the
    store directly use ``_profiles_path()`` / ``_keys_path()`` so they
    target the same resolved path the code reads.

    The builtin-preset catalogue caches its merged result and a
    "package scan done" flag in module globals; both are reset around
    the test and ``list_packages`` is stubbed empty so the builtin set
    is the only preset source. ``CodexTokens.load`` is stubbed to
    ``None`` so codex availability stays deterministic.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False
    monkeypatch.setattr(presets_mod, "list_packages", lambda: [])
    monkeypatch.setattr(CodexTokens, "load", classmethod(lambda cls, path=None: None))

    for env in ak.PROVIDER_KEY_MAP.values():
        monkeypatch.delenv(env, raising=False)
    ak.clear_api_key_resolver()

    yield

    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False


# ---------------------------------------------------------------------------
# A real tool, used to drive the native tool-schema builder.
# ---------------------------------------------------------------------------


class _TranslateTool(BaseTool):
    """A real ``BaseTool`` with no builtin-schema entry.

    Because ``translate`` is not in ``llm/tool_schemas.py:_BUILTIN_SCHEMAS``,
    ``build_tool_schemas`` falls through to the tool's own
    ``get_parameters_schema()`` — the path custom / package tools take.
    """

    @property
    def tool_name(self) -> str:
        return "translate"

    @property
    def description(self) -> str:
        return "Translate text between two languages."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "target_lang": {
                    "type": "string",
                    "description": "Target language code",
                },
            },
            "required": ["text", "target_lang"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="translated", exit_code=0)


class _NoSchemaTool(BaseTool):
    """A tool that does NOT override ``get_parameters_schema`` — the
    schema builder must fall through to its generic single-``content``
    fallback schema."""

    @property
    def tool_name(self) -> str:
        return "noschema"

    @property
    def description(self) -> str:
        return "A tool with no declared parameter schema."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        # Return an empty dict so the builder treats it as "no schema"
        # and uses the generic fallback.
        return {}

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="ok", exit_code=0)


class _ProviderNativeTool(BaseTool):
    """A provider-native tool — ``build_tool_schemas`` must SKIP it (it
    is not a callable function), and ``build_provider_native_tools``
    must collect it for the provider to translate."""

    is_provider_native = True

    @property
    def tool_name(self) -> str:
        return "image_gen"

    @property
    def description(self) -> str:
        return "Generate an image (provider-native)."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="image", exit_code=0)


class _BrokenSchemaTool(BaseTool):
    """A tool whose ``get_parameters_schema`` RAISES — ``build_tool_schemas``
    must swallow the error (log a warning) and fall through to the
    generic single-``content`` fallback rather than crashing the whole
    schema build."""

    @property
    def tool_name(self) -> str:
        return "brokenschema"

    @property
    def description(self) -> str:
        return "A tool whose schema accessor raises."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        raise RuntimeError("schema accessor blew up")

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="ok", exit_code=0)


class _MinimalProvider(BaseLLMProvider):
    """A real ``BaseLLMProvider`` subclass — the smallest thing that
    satisfies the streaming + completion contract. Used to drive the
    deterministic ``llm/base.py`` surface (``_normalize_messages``,
    ``chat`` / ``chat_complete`` delegation, ``with_model``, the
    emergency-drop callback plumbing, the ``last_*`` properties)
    without a live network client.

    ``_stream_chat`` echoes a deterministic chunk per message; the
    completion path returns a fixed ``ChatResponse``. Real provider
    subclasses do exactly this, just talking to a network endpoint
    instead of returning constants.
    """

    provider_name = "minimal"
    provider_native_tools = frozenset({"image_gen"})

    async def _stream_chat(self, messages, *, tools=None, **kwargs):
        # Surface what the base class normalized for us — proves
        # _normalize_messages ran and `chat` delegated here.
        yield f"streamed:{len(messages)}"
        if tools:
            yield f"|tools:{len(tools)}"

    async def _complete_chat(self, messages, **kwargs):
        return ChatResponse(
            content=f"completed:{len(messages)}",
            finish_reason="stop",
            usage={"total_tokens": 7},
            model=self.config.model,
        )


class TestLlmIntegration:
    """Each method is one complete, self-contained ``llm/`` workflow."""

    def test_define_save_reload_resolve_profile_workflow(self):
        """The full config lifecycle ``bootstrap/llm.py`` sits on top of.

        Define a custom backend + a preset that binds to it -> persist
        both to the real YAML store -> reload from disk and assert the
        backend/preset survived byte-exact -> resolve the preset by name
        the way ``create_llm_provider`` does (``resolve_controller_llm``)
        and assert every field of the resulting ``LLMProfile`` -> apply
        an inline controller override and assert the merge -> set the
        preset as the default model and assert no-args resolution finds
        it.
        """
        # 1. Define a custom OpenAI-compatible backend + a preset on it.
        backend = LLMBackend(
            name="acme",
            backend_type="openai",
            base_url="https://acme.example/v1",
            api_key_env="ACME_API_KEY",
            provider_native_tools=["image_gen"],
        )
        preset = LLMPreset(
            name="acme-fast",
            model="acme-model-1",
            provider="acme",
            max_context=128000,
            max_output=8192,
            temperature=0.3,
            reasoning_effort="high",
            extra_body={"foo": {"bar": 1}},
        )

        # 2. Persist to the real YAML store on disk.
        save_backend(backend)
        save_profile(preset)

        # 3. Reload from disk — backend round-trips byte-exact.
        reloaded_backends = backends_mod.load_backends()
        assert "acme" in reloaded_backends
        rb = reloaded_backends["acme"]
        assert rb.backend_type == "openai"
        assert rb.base_url == "https://acme.example/v1"
        assert rb.api_key_env == "ACME_API_KEY"
        # User backends default provider_name to their own name.
        assert rb.provider_name == "acme"
        assert rb.provider_native_tools == ["image_gen"]

        # ...and the preset resolves into a profile keyed by (provider, name).
        profiles = load_profiles()
        assert ("acme", "acme-fast") in profiles
        rp = profiles[("acme", "acme-fast")]
        assert rp.model == "acme-model-1"
        assert rp.provider == "acme"
        assert rp.backend_type == "openai"
        assert rp.base_url == "https://acme.example/v1"
        assert rp.api_key_env == "ACME_API_KEY"
        assert rp.max_context == 128000
        assert rp.max_output == 8192
        assert rp.temperature == 0.3
        assert rp.reasoning_effort == "high"
        assert rp.extra_body == {"foo": {"bar": 1}}
        # The backend's native-tool opt-in is carried onto the profile so
        # bootstrap/llm.py can stamp it on the provider instance.
        assert rp.backend_native_tools == ["image_gen"]
        assert rp.backend_provider_name == "acme"

        # 4. Resolve by name exactly how bootstrap/llm.py does: it builds a
        #    controller-config dict and calls resolve_controller_llm.
        controller_data = {"llm": "acme/acme-fast"}
        resolved = resolve_controller_llm(controller_data, llm_override=None)
        assert resolved is not None
        assert resolved.name == "acme-fast"
        assert resolved.model == "acme-model-1"
        assert resolved.provider == "acme"
        assert resolved.backend_type == "openai"
        assert resolved.base_url == "https://acme.example/v1"
        assert resolved.temperature == 0.3
        # Canonical selector string round-trips back into resolution input.
        assert profile_to_identifier(resolved) == "acme/acme-fast"

        # 5. Inline controller overrides win over the preset's stored
        #    values — the backward-compat merge in resolve_controller_llm.
        overridden = resolve_controller_llm(
            {
                "llm": "acme/acme-fast",
                "temperature": 0.9,
                "max_tokens": 4096,
                "reasoning_effort": "low",
                "extra_body": {"foo": {"baz": 2}},
            }
        )
        assert overridden is not None
        assert overridden.temperature == 0.9
        assert overridden.max_output == 4096  # max_tokens maps to max_output
        assert overridden.reasoning_effort == "low"
        # extra_body is deep-merged, not replaced.
        assert overridden.extra_body == {"foo": {"bar": 1, "baz": 2}}
        # The stored preset is untouched by the override path.
        assert load_profiles()[("acme", "acme-fast")].temperature == 0.3

        # 6. Make it the default and resolve with an empty controller config
        #    — the no-llm-configured path bootstrap/llm.py also exercises.
        set_default_model("acme/acme-fast")
        default_resolved = resolve_controller_llm({})
        assert default_resolved is not None
        assert default_resolved.name == "acme-fast"
        assert default_resolved.provider == "acme"

        # A name that does not exist resolves to None (bootstrap then falls
        # back to inline config / raises).
        assert resolve_controller_llm({"llm": "acme/does-not-exist"}) is None

        # 7. ``get_profile`` / ``get_preset`` are the lookup-by-name
        #    entrypoints the ``/model`` command + web pickers call.
        fetched = get_profile("acme-fast", provider="acme")
        assert fetched is not None and fetched.model == "acme-model-1"
        assert get_preset("acme/acme-fast") is not None
        assert get_profile("acme-fast", provider="nope") is None

        # 8. Resolve by raw ``model:`` id — the legacy controller-config
        #    shape ``_find_profile_by_model`` handles. The acme preset's
        #    model is unique so a bare ``model`` resolves it unambiguously.
        by_model = resolve_controller_llm({"model": "acme-model-1"})
        assert by_model is not None
        assert by_model.name == "acme-fast"

        # 9. Two presets sharing a bare name under DIFFERENT providers:
        #    a bare-name lookup is ambiguous and raises; a qualified
        #    ``provider/name`` lookup is exact.
        save_backend(
            LLMBackend(name="acme2", backend_type="openai", base_url="https://a2/v1")
        )
        save_profile(LLMPreset(name="acme-fast", model="acme2-model", provider="acme2"))
        with pytest.raises(ValueError, match="exists under multiple providers"):
            resolve_controller_llm({"llm": "acme-fast"})
        # Qualified lookups stay unambiguous.
        assert resolve_controller_llm({"llm": "acme/acme-fast"}).model == (
            "acme-model-1"
        )
        assert resolve_controller_llm({"llm": "acme2/acme-fast"}).model == (
            "acme2-model"
        )

        # 10. ``list_all`` enumerates every user + builtin preset; both
        #     acme presets must appear, keyed by (provider, name).
        listed = list_all()
        acme_entries = {
            (e["provider"], e["name"])
            for e in listed
            if e["provider"] in ("acme", "acme2")
        }
        assert acme_entries == {("acme", "acme-fast"), ("acme2", "acme-fast")}

        # 11. ``delete_backend`` refuses while a preset still binds to it,
        #     and refuses to drop a built-in provider entirely.
        with pytest.raises(ValueError, match="still in use"):
            delete_backend("acme2")
        with pytest.raises(ValueError, match="built-in provider"):
            delete_backend("openai")
        # Drop the preset, THEN the backend deletes cleanly.
        assert delete_profile("acme-fast", provider="acme2") is True
        assert delete_backend("acme2") is True
        assert "acme2" not in backends_mod.load_backends()
        # A bare-name delete with the name now unique under one provider.
        assert delete_profile("acme-fast") is True
        assert ("acme", "acme-fast") not in load_profiles()
        # Deleting something already gone -> False (not an error).
        assert delete_profile("acme-fast", provider="acme") is False
        assert delete_backend("never-existed") is False

        # 12. The dataclass round-trips ``bootstrap`` relies on:
        #     LLMProfile.to_dict / from_dict must be byte-stable, and a
        #     legacy ``provider`` value that is really a backend_type
        #     (``anthropic``) is migrated into ``backend_type`` on load.
        profile = LLMProfile(
            name="rt",
            model="rt-model",
            provider="acme",
            backend_type="openai",
            temperature=0.5,
            reasoning_effort="high",
            extra_body={"k": 1},
            backend_native_tools=["image_gen"],
        )
        rt = LLMProfile.from_dict("rt", profile.to_dict())
        assert rt.to_dict() == profile.to_dict()
        legacy_profile = LLMProfile.from_dict(
            "legacy", {"model": "m", "provider": "anthropic"}
        )
        assert legacy_profile.backend_type == "anthropic"
        assert legacy_profile.provider == ""
        # A non-list ``backend_native_tools`` is coerced to an empty list.
        coerced = LLMProfile.from_dict(
            "c", {"model": "m", "backend_native_tools": "not-a-list"}
        )
        assert coerced.backend_native_tools == []
        # LLMPreset round-trip preserves variation_groups.
        vp = LLMPreset(
            name="vp",
            model="vp-model",
            provider="acme",
            variation_groups={"speed": {"fast": {"temperature": 0.1}}},
        )
        vp_rt = LLMPreset.from_dict("vp", vp.to_dict())
        assert vp_rt.variation_groups == {"speed": {"fast": {"temperature": 0.1}}}
        # LLMBackend.from_dict coerces a non-list provider_native_tools.
        bd = LLMBackend.from_dict(
            "bd", {"backend_type": "openai", "provider_native_tools": "x"}
        )
        assert bd.provider_native_tools == []

        # 13. ``llm/variations`` — the variation-selector machinery
        #     ``resolve_controller_llm`` applies when a preset declares
        #     ``variation_groups``. Save a preset with two groups, then
        #     resolve it with a ``provider/name@group=option`` selector
        #     and assert the patch landed on the resolved profile.
        save_backend(
            LLMBackend(name="varb", backend_type="openai", base_url="https://v/v1")
        )
        save_profile(
            LLMPreset(
                name="varp",
                model="var-model",
                provider="varb",
                temperature=0.5,
                max_output=1000,
                variation_groups={
                    "speed": {
                        "fast": {"temperature": 0.1, "max_output": 256},
                        "slow": {"temperature": 0.9},
                    },
                    "effort": {"hi": {"reasoning_effort": "high"}},
                },
            )
        )
        # No selector -> the preset's stored base values.
        plain_var = resolve_controller_llm({"llm": "varb/varp"})
        assert plain_var.temperature == 0.5
        assert plain_var.max_output == 1000
        assert plain_var.selected_variations == {}
        # ``@speed=fast`` -> the fast option's patch map is applied.
        fast_var = resolve_controller_llm({"llm": "varb/varp@speed=fast"})
        assert fast_var.temperature == 0.1
        assert fast_var.max_output == 256
        assert fast_var.selected_variations == {"speed": "fast"}
        # The canonical identifier round-trips the selection back.
        assert profile_to_identifier(fast_var) == "varb/varp@speed=fast"
        # Two groups at once -> both patches applied; order-independent.
        both_var = resolve_controller_llm({"llm": "varb/varp@speed=slow,effort=hi"})
        assert both_var.temperature == 0.9
        assert both_var.reasoning_effort == "high"
        assert both_var.selected_variations == {"speed": "slow", "effort": "hi"}
        # The single-token shorthand ``@fast`` resolves against the one
        # group that owns a ``fast`` option.
        shorthand_var = resolve_controller_llm({"llm": "varb/varp@fast"})
        assert shorthand_var.temperature == 0.1
        # An unknown group / option is a hard ValueError naming the preset.
        with pytest.raises(ValueError, match="Unknown variation group"):
            resolve_controller_llm({"llm": "varb/varp@nosuch=x"})
        with pytest.raises(ValueError, match="Unknown variation option"):
            resolve_controller_llm({"llm": "varb/varp@speed=warp"})
        # The stored preset is untouched by any selector resolution.
        assert load_profiles()[("varb", "varp")].temperature == 0.5
        delete_profile("varp", provider="varb")
        delete_backend("varb")

        # 14. The pure ``variations`` helpers, exercised directly — the
        #     parsing / patch / merge units ``_resolve_preset`` sits on.
        # parse_variation_selector splits name + selections.
        assert parse_variation_selector("p/n") == ("p/n", {})
        assert parse_variation_selector("p/n@g=o,h=p") == (
            "p/n",
            {"g": "o", "h": "p"},
        )
        # A bare ``@token`` is stored under the internal shorthand key.
        base, sel = parse_variation_selector("p@token")
        assert base == "p" and len(sel) == 1
        # Malformed selectors raise with explicit messages.
        with pytest.raises(ValueError, match="missing a preset"):
            parse_variation_selector("@g=o")
        with pytest.raises(ValueError, match="is empty"):
            parse_variation_selector("p@")
        with pytest.raises(ValueError, match="Invalid empty variation"):
            parse_variation_selector("p@g=o,")
        with pytest.raises(ValueError, match="only specify one option"):
            parse_variation_selector("p@one,two")
        # apply_patch_map: dotted paths land; a disallowed root is rejected.
        patched = apply_patch_map(
            {"temperature": 0.5}, {"temperature": 0.2, "extra_body.foo": 1}
        )
        assert patched == {"temperature": 0.2, "extra_body": {"foo": 1}}
        with pytest.raises(ValueError, match="Unsupported variation patch target"):
            apply_patch_map({}, {"forbidden_root": 1})
        # apply_variation_groups: two selections writing the SAME dotted
        # path is a conflict, not last-writer-wins.
        groups = {
            "a": {"x": {"temperature": 0.1}},
            "b": {"y": {"temperature": 0.9}},
        }
        with pytest.raises(ValueError, match="conflict on 'temperature'"):
            apply_variation_groups({}, groups, {"a": "x", "b": "y"})
        # normalize_variation_selections: an ambiguous shorthand (matches
        # two groups) demands disambiguation.
        ambig_preset = LLMPreset(
            name="amb",
            model="m",
            provider="p",
            variation_groups={
                "g1": {"shared": {"temperature": 0.1}},
                "g2": {"shared": {"temperature": 0.2}},
            },
        )
        with pytest.raises(ValueError, match="Ambiguous variation option"):
            normalize_variation_selections({"__option__": "shared"}, ambig_preset)
        # deep_merge_dicts recurses into nested dicts, replaces scalars.
        assert deep_merge_dicts(
            {"a": {"b": 1, "c": 2}, "d": 9}, {"a": {"c": 3, "e": 4}}
        ) == {"a": {"b": 1, "c": 3, "e": 4}, "d": 9}

    def test_legacy_profiles_only_file_and_builtin_resolution_workflow(
        self, monkeypatch
    ):
        """Read a pre-2026-05 ``profiles:``-only file, then a builtin.

        The legacy YAML shape stored user presets under a flat
        ``profiles:`` key with NO ``presets:`` key at all. ``load_presets``
        must still find them via its legacy-merge fallback. Then resolve
        a built-in preset (``codex/gpt-5.4``) the way ``kt model`` /
        live-switching does, asserting the backend_type carried from the
        built-in ``codex`` backend.
        """
        # 1. Hand-write a legacy file: a ``profiles:`` block, no ``presets:``.
        backends_mod.save_yaml_store(
            {
                "version": 2,
                "profiles": {
                    "old-or": {
                        "model": "anthropic/claude-legacy",
                        "provider": "openrouter",
                        "max_context": 200000,
                        "max_output": 16384,
                    }
                },
            }
        )

        # 2. The legacy preset is recovered keyed by (provider, name).
        profiles = load_profiles()
        assert ("openrouter", "old-or") in profiles
        legacy = profiles[("openrouter", "old-or")]
        assert legacy.model == "anthropic/claude-legacy"
        assert legacy.provider == "openrouter"
        # openrouter is a built-in backend — its transport is carried in.
        assert legacy.backend_type == "openai"
        assert legacy.base_url == "https://openrouter.ai/api/v1"
        assert legacy.api_key_env == "OPENROUTER_API_KEY"
        assert legacy.max_context == 200000

        # 3. Resolve it by name through the same entrypoint bootstrap uses.
        resolved_legacy = resolve_controller_llm({"llm": "openrouter/old-or"})
        assert resolved_legacy is not None
        assert resolved_legacy.model == "anthropic/claude-legacy"

        # 4. A built-in preset resolves with the built-in backend's
        #    transport metadata — codex's bespoke backend_type.
        codex_profile = resolve_controller_llm({}, llm_override="codex/gpt-5.4")
        assert codex_profile is not None
        assert codex_profile.name == "gpt-5.4"
        assert codex_profile.provider == "codex"
        assert codex_profile.backend_type == "codex"
        # bootstrap/llm.py branches on backend_type == "codex" -> CodexOAuthProvider.

        # 5. A built-in anthropic preset resolves to the anthropic backend.
        claude_profile = resolve_controller_llm(
            {}, llm_override="anthropic/claude-opus-4.7"
        )
        assert claude_profile is not None
        assert claude_profile.backend_type == "anthropic"
        assert claude_profile.base_url == "https://api.anthropic.com"
        assert claude_profile.model == "claude-opus-4-7"

        # 6. backend_type normalization + validation — the exact rules
        #    ``save_backend`` enforces on every write.
        assert _normalize_backend_type("codex-oauth") == "codex"  # legacy alias
        assert _normalize_backend_type("") == "openai"  # empty -> safe default
        assert _normalize_backend_type("anthropic") == "anthropic"
        assert validate_backend_type("codex-oauth") == "codex"
        with pytest.raises(ValueError, match="Unsupported backend_type"):
            validate_backend_type("telepathy")

        # 7. ``legacy_provider_from_data`` — the best-effort inference
        #    that lets pre-2026-04 inline preset shapes still resolve.
        assert (
            legacy_provider_from_data({"base_url": "https://openrouter.ai/api/v1"})
            == "openrouter"
        )
        assert (
            legacy_provider_from_data({"base_url": "https://api.anthropic.com"})
            == "anthropic"
        )
        assert (
            legacy_provider_from_data(
                {"base_url": "https://generativelanguage.googleapis.com/x"}
            )
            == "gemini"
        )
        assert (
            legacy_provider_from_data({"base_url": "https://api.openai.com/v1"})
            == "openai"
        )
        assert legacy_provider_from_data({"provider": "codex-oauth"}) == "codex"
        # An explicit non-backend-type provider value passes straight
        # through.
        assert legacy_provider_from_data({"provider": "my-custom"}) == "my-custom"
        # api_key_env inference when no base_url hints at the provider.
        assert legacy_provider_from_data({"api_key_env": "MIMO_API_KEY"}) == "mimo"
        # Nothing identifiable -> empty string.
        assert legacy_provider_from_data({}) == ""

        # 8. A corrupt YAML store -> load returns ``{}`` (warns, no
        #    crash), so a damaged file degrades to "no user presets"
        #    rather than taking the process down.
        backends_mod._profiles_path().write_text(
            "this: is: not: valid: yaml: [", encoding="utf-8"
        )
        assert backends_mod.load_yaml_store() == {}
        assert load_profiles() == {}

        # 9. A NESTED-shape presets file (the current write format) is
        #    read back by ``load_presets`` directly — both layouts are
        #    accepted on read.
        backends_mod.save_yaml_store(
            {
                "version": 3,
                "presets": {
                    "openai": {
                        "nested-one": {"model": "gpt-nested", "max_context": 4096}
                    }
                },
            }
        )
        nested_profiles = load_profiles()
        assert ("openai", "nested-one") in nested_profiles
        assert nested_profiles[("openai", "nested-one")].model == "gpt-nested"

        # 10. ``iter_all_presets`` + ``resolve_alias`` — the catalogue
        #     iteration + alias lookup ``kt model`` drives.
        all_presets = iter_all_presets()
        assert any(
            provider == "codex" and name == "gpt-5.4"
            for provider, name, _ in all_presets
        )
        # A known alias maps to its (provider, canonical) pair...
        aliased = resolve_alias("gpt-5.4-api")
        assert aliased == ("openai", "gpt-5.4")
        # ...and a non-alias name resolves to None (treat as canonical).
        assert resolve_alias("definitely-not-an-alias") is None

        # 11. Package-declared ``llm_presets`` — ``presets._merge_package_presets``
        #     scans installed packages and folds NEW (provider, name)
        #     entries into the catalogue without overriding builtins.
        #     The autouse fixture stubbed ``list_packages`` empty; here
        #     we hand it a real package manifest and re-prime the cache.
        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False
        monkeypatch.setattr(
            presets_mod,
            "list_packages",
            lambda: [
                {
                    "name": "kt-extra-models",
                    "llm_presets": [
                        {
                            "name": "pkg-fast",
                            "provider": "pkgprov",
                            "model": "pkg-model-1",
                            "max_context": 64000,
                        },
                        # Missing provider -> skipped entirely.
                        {"name": "no-provider", "model": "x"},
                        # A non-dict entry -> skipped without crashing.
                        "garbage",
                        # Re-declares a builtin (codex/gpt-5.4) -> the
                        # builtin wins, the package entry is dropped.
                        {
                            "name": "gpt-5.4",
                            "provider": "codex",
                            "model": "hijacked",
                        },
                    ],
                }
            ],
        )
        merged_catalogue = iter_all_presets()
        by_key = {(p, n): d for p, n, d in merged_catalogue}
        # The brand-new package preset appears under its declared provider.
        assert ("pkgprov", "pkg-fast") in by_key
        assert by_key[("pkgprov", "pkg-fast")]["model"] == "pkg-model-1"
        # The provider-less and garbage entries were silently skipped.
        assert not any(n == "no-provider" for (_p, n) in by_key)
        # The builtin codex/gpt-5.4 was NOT overridden by the package.
        assert by_key[("codex", "gpt-5.4")]["model"] != "hijacked"
        # A second call is served from the cache (merge flag latched).
        assert presets_mod._package_presets_merged is True
        # Reset so the autouse-fixture teardown leaves a clean slate.
        presets_mod._all_presets_cache = None
        presets_mod._package_presets_merged = False

    def test_build_tool_schemas_from_registry_workflow(self):
        """Drive the native tool-schema builder over a real registry.

        ``bootstrap/tools.py`` registers tools, then the controller calls
        ``build_tool_schemas`` to hand the LLM provider its native
        function-calling list. This workflow registers one tool that HAS
        a builtin schema (``read``) and one that does NOT (``translate``,
        falling through to ``get_parameters_schema``), plus a sub-agent,
        then asserts the exact emitted schema for each.
        """
        registry = Registry()

        # ``read`` is a real builtin tool with an entry in _BUILTIN_SCHEMAS.
        from kohakuterrarium.builtins.tools.read import ReadTool

        registry.register_tool(ReadTool())
        registry.register_tool(_TranslateTool())
        registry.register_subagent("researcher", _FakeSubAgent("Investigates topics."))

        schemas = build_tool_schemas(registry)
        by_name = {s.name: s for s in schemas}
        assert set(by_name) == {"read", "translate", "researcher"}

        # ``read`` uses the builtin schema, with ``run_in_background``
        # injected by the builder onto every tool.
        read_schema = by_name["read"]
        assert isinstance(read_schema, ToolSchema)
        assert read_schema.parameters == {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "offset": {"type": "integer", "description": "Line offset (optional)"},
                "limit": {"type": "integer", "description": "Max lines (optional)"},
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true, run in background. Results delivered "
                        "later, not immediately."
                    ),
                },
            },
            "required": ["path"],
        }
        # The OpenAI wire format wraps the schema under type/function.
        assert read_schema.to_api_format() == {
            "type": "function",
            "function": {
                "name": "read",
                "description": read_schema.description,
                "parameters": read_schema.parameters,
            },
        }

        # ``translate`` has no builtin schema -> the builder used the
        # tool's own get_parameters_schema(), then injected run_in_background.
        translate_schema = by_name["translate"]
        assert translate_schema.description == "Translate text between two languages."
        assert translate_schema.parameters == {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "target_lang": {
                    "type": "string",
                    "description": "Target language code",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true, run in background. Results delivered "
                        "later, not immediately."
                    ),
                },
            },
            "required": ["text", "target_lang"],
        }

        # Sub-agents are emitted as callable functions with a fixed
        # ``task`` / ``run_in_background`` schema.
        researcher_schema = by_name["researcher"]
        assert researcher_schema.description == "Investigates topics."
        assert researcher_schema.parameters == {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task description for the sub-agent",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true (default), run in background — result "
                        "delivered later. If false, block and wait for "
                        "the sub-agent to finish before continuing."
                    ),
                },
            },
            "required": ["task"],
        }

        # --- a tool with no schema at all -> the generic single-
        # ``content`` fallback, with run_in_background injected. And a
        # provider-native tool -> SKIPPED from the function schema list.
        registry.register_tool(_NoSchemaTool())
        registry.register_tool(_ProviderNativeTool())
        schemas2 = build_tool_schemas(registry)
        by_name2 = {s.name: s for s in schemas2}
        # The provider-native ``image_gen`` is NOT a callable function.
        assert "image_gen" not in by_name2
        # ``noschema`` got the generic fallback shape.
        assert by_name2["noschema"].parameters == {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Input content for the tool",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true, run in background. Results delivered "
                        "later, not immediately."
                    ),
                },
            },
        }
        # ``build_provider_native_tools`` collects exactly the native one.
        native = build_provider_native_tools(registry)
        assert [t.tool_name for t in native] == ["image_gen"]

        # --- a tool whose ``get_parameters_schema`` RAISES: the builder
        # must log + swallow the error and fall through to the generic
        # ``content`` fallback, never letting one bad tool abort the
        # whole schema build.
        registry.register_tool(_BrokenSchemaTool())
        schemas3 = build_tool_schemas(registry)
        by_name3 = {s.name: s for s in schemas3}
        # The broken tool still produced a (fallback) schema...
        assert by_name3["brokenschema"].parameters == {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Input content for the tool",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "If true, run in background. Results delivered "
                        "later, not immediately."
                    ),
                },
            },
        }
        # ...and every other tool's schema is unaffected by its neighbour.
        assert by_name3["read"].parameters == read_schema.parameters

        # ``NativeToolCall.parsed_arguments`` — the controller parses the
        # API-returned JSON arg string; malformed JSON degrades to a
        # ``{"_raw": ...}`` capture instead of raising.
        good_call = NativeToolCall(id="c1", name="read", arguments='{"path": "x.py"}')
        assert good_call.parsed_arguments() == {"path": "x.py"}
        bad_call = NativeToolCall(id="c2", name="read", arguments="{not json")
        assert bad_call.parsed_arguments() == {"_raw": "{not json"}

    async def test_multimodal_message_round_trip_workflow(self):
        """Build a full multimodal conversation and assert exact wire shape.

        The controller assembles ``Message`` objects (system + multimodal
        user + assistant + tool result), serializes them to OpenAI wire
        dicts before an LLM call, and reconstructs ``Message`` objects
        from the API response. This workflow exercises every leg:
        ``make_multimodal_content`` -> ``to_dict`` -> ``from_dict``,
        including the ``extra_fields`` capture for reasoning models.
        """
        # 1. Assemble a conversation the way the controller does.
        system = SystemMessage("You are a vision assistant.")

        image = ImagePart(
            url="data:image/png;base64,AAAA",
            detail="high",
            source_type="attachment",
            source_name="diagram.png",
        )
        user_content = make_multimodal_content("Describe this:", images=[image])
        # An image was supplied -> multimodal list form, text-first.
        assert user_content == [TextPart(text="Describe this:"), image]
        user = UserMessage(user_content, name="alice")

        # An assistant message carrying a provider-specific reasoning field
        # — captured into extra_fields so it round-trips on the next turn.
        assistant = AssistantMessage(
            "It is a flowchart.",
            extra_fields={"reasoning_content": "looked at the shapes"},
        )

        tool_msg = ToolMessage("done", tool_call_id="call_42", name="translate")

        conversation = [system, user, assistant, tool_msg]
        wire = [m.to_dict() for m in conversation]

        # 2. Assert the exact OpenAI wire dicts.
        assert wire[0] == {"role": "system", "content": "You are a vision assistant."}
        assert wire[1] == {
            "role": "user",
            "name": "alice",
            "content": [
                {"type": "text", "text": "Describe this:"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,AAAA",
                        "detail": "high",
                    },
                    "meta": {
                        "source_type": "attachment",
                        "source_name": "diagram.png",
                    },
                },
            ],
        }
        # extra_fields are echoed as top-level keys, never clobbering role.
        assert wire[2] == {
            "role": "assistant",
            "content": "It is a flowchart.",
            "reasoning_content": "looked at the shapes",
        }
        assert wire[3] == {
            "role": "tool",
            "content": "done",
            "name": "translate",
            "tool_call_id": "call_42",
        }

        # 3. Reconstruct from wire dicts — the resume / API-response path.
        rebuilt = [Message.from_dict(d) for d in wire]

        assert isinstance(rebuilt[1].content, list)
        assert rebuilt[1].is_multimodal() is True
        assert rebuilt[1].has_images() is True
        rebuilt_image = rebuilt[1].get_images()[0]
        assert rebuilt_image.url == "data:image/png;base64,AAAA"
        assert rebuilt_image.detail == "high"
        assert rebuilt_image.source_type == "attachment"
        assert rebuilt_image.source_name == "diagram.png"
        assert rebuilt[1].get_text_content() == "Describe this:"

        # The reasoning field survived the round-trip into extra_fields.
        assert rebuilt[2].extra_fields == {"reasoning_content": "looked at the shapes"}
        assert rebuilt[2].get_text_content() == "It is a flowchart."

        # 4. A second serialization is byte-identical — the round-trip is
        #    stable, which is what makes session resume safe.
        assert [m.to_dict() for m in rebuilt] == wire

        # Text-only content stays a plain string, never a list.
        assert make_multimodal_content("just text", images=None) == "just text"
        assert UserMessage("plain").to_dict() == {
            "role": "user",
            "content": "plain",
        }
        # ``prepend_images=True`` puts the image BEFORE the text part.
        prepended = make_multimodal_content(
            "after", images=[image], prepend_images=True
        )
        assert prepended == [image, TextPart(text="after")]

        # 5. ``create_message`` is the factory the controller / resume
        #    path uses — every role maps to its right subclass, and a
        #    ``tool`` role without ``tool_call_id`` is a hard error.
        assert isinstance(create_message("system", "sys"), SystemMessage)
        assert isinstance(create_message("user", "u"), UserMessage)
        assert isinstance(create_message("assistant", "a"), AssistantMessage)
        assert isinstance(create_message("tool", "t", tool_call_id="c1"), ToolMessage)
        with pytest.raises(ValueError, match="requires tool_call_id"):
            create_message("tool", "t")
        # A system message built from a list of text parts is flattened
        # to a plain string (system messages are always text-only).
        sys_from_list = create_message(
            "system", [TextPart(text="line1"), TextPart(text="line2")]
        )
        assert sys_from_list.content == "line1\nline2"
        # An assistant message with a NON-text part in its list keeps the
        # list form (a generated image must survive serialization).
        asst_img = create_message("assistant", [TextPart(text="see:"), image])
        assert isinstance(asst_img.content, list)
        # An unknown role falls through to a bare Message.
        misc = create_message("function", "x")
        assert type(misc) is Message and misc.role == "function"

        # 6. ``FilePart`` — the custom file-reference part. It survives
        #    the to_dict / content_part_from_dict round-trip.
        file_part = FilePart(
            path="src/main.py",
            name="main.py",
            content="print('hi')",
            mime="text/x-python",
            encoding="utf-8",
            is_inline=True,
        )
        file_user = UserMessage([TextPart(text="this file:"), file_part])
        file_wire = file_user.to_dict()
        assert file_wire["content"][1] == {
            "type": "file",
            "file": {
                "path": "src/main.py",
                "name": "main.py",
                "content": "print('hi')",
                "mime": "text/x-python",
                "data_base64": None,
                "encoding": "utf-8",
                "is_inline": True,
            },
        }
        rebuilt_file = Message.from_dict(file_wire)
        assert isinstance(rebuilt_file.content[1], FilePart)
        assert rebuilt_file.content[1].path == "src/main.py"
        assert rebuilt_file.content[1].is_inline is True

        # 7. ``ImagePart.get_description`` — the human-readable label the
        #    logging / context-length code uses for a non-text part.
        assert image.get_description() == "[attachment: diagram.png]"
        assert ImagePart(url="u", source_type="emoji").get_description() == "[emoji]"
        assert ImagePart(url="u").get_description() == "[image]"

        # 8. ``messages_to_dicts`` / ``dicts_to_messages`` — the bulk
        #    conversation (de)serialization the controller + resume use.
        bulk = [SystemMessage("s"), UserMessage("u"), AssistantMessage("a")]
        bulk_dicts = messages_to_dicts(bulk)
        assert [d["role"] for d in bulk_dicts] == ["system", "user", "assistant"]
        bulk_back = dicts_to_messages(bulk_dicts)
        assert [m.role for m in bulk_back] == ["system", "user", "assistant"]
        # messages_to_dicts also tolerates raw dicts mixed in (resumed
        # sessions store some messages pre-serialized).
        mixed = messages_to_dicts([{"role": "user", "content": "raw"}, bulk[0]])
        assert mixed == [
            {"role": "user", "content": "raw"},
            {"role": "system", "content": "s"},
        ]

        # 9. A message with explicit ``None`` content and a message with
        #    ``tool_calls`` — the two assistant-message wire shapes the
        #    controller emits when the LLM returns native tool calls.
        none_msg = Message(role="assistant", content=None)
        assert none_msg.to_dict() == {"role": "assistant", "content": None}
        tc_msg = Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "x"}}],
        )
        tc_wire = tc_msg.to_dict()
        assert tc_wire["tool_calls"][0]["id"] == "c1"
        # has_images / get_images on a plain-string message are safe no-ops.
        assert UserMessage("plain text").has_images() is False
        assert UserMessage("plain text").get_images() == []
        assert UserMessage("plain text").get_text_content() == "plain text"
        # Regression guard for B-fat-misc-1 (FIXED): ``content=None`` is a
        # valid wire shape (``Message.to_dict`` emits it for native-tool-
        # call assistant turns). ``has_images`` / ``get_images`` /
        # ``get_text_content`` used to special-case only ``str`` and fall
        # through to ``for part in self.content`` → ``TypeError``. They
        # now treat ``None`` like empty content.
        assert none_msg.has_images() is False
        assert none_msg.get_images() == []
        assert none_msg.get_text_content() == ""

        # 10. ``llm/base.BaseLLMProvider`` — the deterministic provider
        #     base class every live provider subclasses. Its job is to
        #     normalize messages and delegate to the subclass's
        #     ``_stream_chat`` / ``_complete_chat``. Drive it through a
        #     minimal real subclass to pin the contract.
        provider = _MinimalProvider(LLMConfig(model="mini-1"))
        # Fresh provider: no tool calls / usage / parts captured yet.
        assert provider.last_tool_calls == []
        assert provider.last_usage == {}
        assert provider.last_assistant_content_parts is None
        assert provider.last_assistant_extra_fields == {}

        # ``chat`` normalizes Message objects -> dicts, then delegates to
        # ``_stream_chat``. The subclass echoes the normalized count.
        streamed = []
        async for piece in provider.chat([SystemMessage("s"), UserMessage("u")]):
            streamed.append(piece)
        assert streamed == ["streamed:2"]
        # ``chat`` also accepts raw dicts (already-normalized) untouched.
        streamed_dicts = []
        async for piece in provider.chat([{"role": "user", "content": "raw"}]):
            streamed_dicts.append(piece)
        assert streamed_dicts == ["streamed:1"]
        # An empty message list normalizes to [] and still delegates.
        empty_stream = [p async for p in provider.chat([])]
        assert empty_stream == ["streamed:0"]
        # ``stream=False`` routes through ``_complete_chat`` -> one yield
        # of the full content.
        nonstream = [p async for p in provider.chat([UserMessage("u")], stream=False)]
        assert nonstream == ["completed:1"]
        # ``chat_complete`` returns the full ``ChatResponse`` object.
        resp = await provider.chat_complete([UserMessage("u"), AssistantMessage("a")])
        assert resp.content == "completed:2"
        assert resp.finish_reason == "stop"
        assert resp.usage == {"total_tokens": 7}
        assert resp.model == "mini-1"

        # ``with_model`` — same name / empty name -> the SAME instance
        # (no-op reuse); a different name on a base provider with no
        # client to re-pool is a hard ValueError.
        assert provider.with_model("mini-1") is provider
        assert provider.with_model("") is provider
        with pytest.raises(ValueError, match="cannot switch to"):
            provider.with_model("other-model")

        # The emergency-drop callback plumbing: a registered callback is
        # invoked with the recovered messages; a callback that raises is
        # swallowed (defensive — one bad callback can't break recovery).
        seen: list[list] = []
        provider.on_emergency_drop(lambda msgs: seen.append(msgs))
        provider.on_emergency_drop(lambda msgs: (_ for _ in ()).throw(RuntimeError()))
        provider._notify_emergency_drop([{"role": "user", "content": "kept"}])
        assert seen == [[{"role": "user", "content": "kept"}]]

        # ``translate_provider_native_tool`` — the base default is None
        # (a provider opts in by overriding); the minimal provider does
        # not, so any tool translates to None.
        assert provider.translate_provider_native_tool(object()) is None

        # The provider-native metadata the agent-start validator reads.
        assert provider.provider_name == "minimal"
        assert provider.provider_native_tools == frozenset({"image_gen"})

    def test_api_key_storage_and_resolution_workflow(self):
        """Store + retrieve an API key, then assert the resolver override.

        ``bootstrap/llm.py`` calls ``get_api_key(provider)`` to find the
        credential for a resolved profile. This workflow drives the full
        ``api_keys`` surface: save to the real YAML file, retrieve by
        provider name AND by env-var name (the normalization path),
        confirm the env-var fallback, then install a process-wide
        resolver and confirm it becomes authoritative (worker mode).
        """
        # 1. Nothing stored yet.
        assert get_api_key("openai") == ""

        # 2. Save a key for a provider, retrieve it back exactly.
        ak.save_api_key("openai", "sk-test-openai-key")
        assert get_api_key("openai") == "sk-test-openai-key"
        # Retrieval also works by the env-var NAME (normalized to provider).
        assert get_api_key("OPENAI_API_KEY") == "sk-test-openai-key"

        # 3. Masked listing never exposes the full secret.
        assert ak.list_api_keys() == {"openai": "sk-t...-key"}

        # 4. A provider with no stored key falls back to the env var.
        import os

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        try:
            assert get_api_key("anthropic") == "sk-ant-from-env"
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        assert get_api_key("anthropic") == ""

        # 5. Worker mode: a registered resolver is AUTHORITATIVE. It is
        #    consulted before the file/env, and a resolver MISS returns ""
        #    immediately — the worker's own file is intentionally ignored.
        ak.register_api_key_resolver(
            lambda provider: "sk-host-key" if provider == "openai" else ""
        )
        try:
            assert get_api_key("openai") == "sk-host-key"
            # openai HAS a stored file key, but the resolver still wins.
            assert get_api_key("anthropic") == ""
        finally:
            ak.clear_api_key_resolver()

        # 6. After clearing the resolver, the file-backed key is visible again.
        assert get_api_key("openai") == "sk-test-openai-key"

        # 7. A SHORT key (<= 8 chars) is masked as ``****`` in the
        #    listing, never partially exposed.
        ak.save_api_key("gemini", "short")
        masked = ak.list_api_keys()
        assert masked["gemini"] == "****"
        assert masked["openai"] == "sk-t...-key"

        # 8. A corrupt api_keys file -> load degrades to ``{}`` (no
        #    crash); ``get_api_key`` then falls straight through to env.
        ak._keys_path().write_text("{not: valid: yaml: [", encoding="utf-8")
        assert ak._load_api_keys() == {}
        assert get_api_key("openai") == ""  # nothing stored, no env var
        # A file whose YAML is valid but NOT a dict also degrades to {}.
        ak._keys_path().write_text("- just\n- a\n- list\n", encoding="utf-8")
        assert ak._load_api_keys() == {}


# ---------------------------------------------------------------------------
# Minimal sub-agent stand-in — build_tool_schemas only reads ``.description``.
# ---------------------------------------------------------------------------


class _FakeSubAgent:
    """Deterministic stand-in for a registered sub-agent.

    ``build_tool_schemas`` only touches ``description`` on sub-agents; a
    full ``SubAgent`` would drag in an LLM provider, so this is the
    correct seam to fake — it stands in for a config-only collaborator,
    not for the unit under test.
    """

    def __init__(self, description: str) -> None:
        self.description = description
