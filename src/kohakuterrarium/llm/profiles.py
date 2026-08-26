"""LLM preset/provider system — preset loading + runtime resolution.

Resolve, persist, and list LLM presets against configured provider backends.
"""

from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.api_keys import KEYS_PATH as KEYS_PATH
from kohakuterrarium.llm.api_keys import KT_DIR as KT_DIR
from kohakuterrarium.llm.api_keys import PROVIDER_KEY_MAP as PROVIDER_KEY_MAP
from kohakuterrarium.llm.api_keys import get_api_key
from kohakuterrarium.llm.api_keys import list_api_keys as list_api_keys
from kohakuterrarium.llm.api_keys import save_api_key
from kohakuterrarium.llm.backends import (
    _BUILTIN_PROVIDER_NAMES,
)
from kohakuterrarium.llm.backends import (
    _LEGACY_BACKEND_TYPE_VALUES as _LEGACY_BACKEND_TYPE_VALUES,
)
from kohakuterrarium.llm.backends import _SCHEMA_VERSION as _SCHEMA_VERSION
from kohakuterrarium.llm.backends import PROFILES_PATH as PROFILES_PATH
from kohakuterrarium.llm.backends import (
    _normalize_backend_type as _normalize_backend_type,
)
from kohakuterrarium.llm.backends import (
    legacy_provider_from_data as _legacy_provider_from_data,
)
from kohakuterrarium.llm.backends import (
    load_backends,
)
from kohakuterrarium.llm.backends import load_yaml_store as _load_yaml
from kohakuterrarium.llm.backends import save_yaml_store as _save_yaml
from kohakuterrarium.llm.backends import (
    validate_backend_type,
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.grok_auth import GrokTokens
from kohakuterrarium.llm.preset_store import get_subagent_models, load_presets
from kohakuterrarium.llm.preset_store import preset_from_data as _preset_from_data
from kohakuterrarium.llm.preset_store import serialize_user_data as _serialize_user_data
from kohakuterrarium.llm.presets import ALIASES as ALIASES
from kohakuterrarium.llm.presets import PRESETS as PRESETS
from kohakuterrarium.llm.presets import get_all_presets, resolve_alias
from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset, LLMProfile
from kohakuterrarium.llm.variations import (
    _SHORTHAND_SELECTION_KEY,
)
from kohakuterrarium.llm.variations import apply_patch_map as apply_patch_map
from kohakuterrarium.llm.variations import (
    apply_variation_groups,
    deep_merge_dicts,
    normalize_variation_selections,
    parse_variation_selector,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def save_backend(backend: LLMBackend) -> None:
    """Persist a user provider after normalizing its backend type."""
    backend.backend_type = validate_backend_type(backend.backend_type)
    data = _load_yaml()
    backends = load_backends()
    presets = load_presets()
    backends[backend.name] = backend
    _save_yaml(
        _serialize_user_data(
            presets,
            backends,
            data.get("default_model", ""),
            get_subagent_models(),
        )
    )


def delete_backend(name: str) -> bool:
    if name in _BUILTIN_PROVIDER_NAMES:
        raise ValueError(f"Cannot delete built-in provider: {name}")
    data = _load_yaml()
    existing = data.get("backends", {}) or data.get("providers", {})
    if name not in existing:
        return False
    presets = load_presets()
    if any(provider == name for provider, _ in presets):
        raise ValueError(f"Provider still in use by one or more presets: {name}")
    backends = load_backends()
    backends.pop(name, None)
    _save_yaml(
        _serialize_user_data(
            presets,
            backends,
            data.get("default_model", ""),
            get_subagent_models(),
        )
    )
    save_api_key(name, "")
    return True


def _resolve_preset(
    preset: LLMPreset,
    backends: dict[str, LLMBackend],
    selections: dict[str, str] | None = None,
) -> LLMProfile | None:
    provider = backends.get(preset.provider) if preset.provider else None
    if preset.provider and provider is None:
        return None

    normalized = normalize_variation_selections(selections or {}, preset)
    resolved_dict = apply_variation_groups(
        preset.to_dict(), preset.variation_groups, normalized
    )
    resolved_preset = LLMPreset.from_dict(preset.name, resolved_dict)
    resolved_preset.provider = preset.provider
    preset_native_tools = resolved_preset.provider_native_tools

    return LLMProfile(
        name=resolved_preset.name,
        model=resolved_preset.model,
        provider=resolved_preset.provider,
        backend_type=provider.backend_type if provider else "",
        max_context=resolved_preset.max_context,
        max_output=resolved_preset.max_output,
        base_url=provider.base_url if provider else "",
        api_key_env=provider.api_key_env if provider else "",
        temperature=resolved_preset.temperature,
        reasoning_effort=resolved_preset.reasoning_effort,
        service_tier=resolved_preset.service_tier,
        extra_body=deepcopy(resolved_preset.extra_body),
        retry_policy=deepcopy(resolved_preset.retry_policy),
        selected_variations=normalized,
        backend_provider_name=provider.provider_name if provider else "",
        backend_native_tools=(
            list(provider.provider_native_tools)
            if preset_native_tools is None and provider
            else list(preset_native_tools or [])
        ),
    )


def load_profiles() -> dict[tuple[str, str], LLMProfile]:
    backends = load_backends()
    profiles: dict[tuple[str, str], LLMProfile] = {}
    for key, preset in load_presets().items():
        resolved = _resolve_preset(preset, backends)
        if resolved is not None:
            profiles[key] = resolved
    return profiles


# The first available provider supplies the implicit default model.
_PROVIDER_DEFAULT_MODELS: list[tuple[str, str]] = [
    ("codex", "gpt-5.5"),
    ("openrouter", "mimo-v2.5-pro"),
    ("anthropic", "claude-opus-4.8"),
    ("openai", "gpt-5.5"),
    ("gemini", "gemini-3.1-pro"),
    ("mimo", "mimo-v2.5-pro"),
    ("kimi-code", "kimi-for-coding"),
    ("glm-coding", "glm-5.2"),
]


# Legacy raw model ids use stable provider preference; explicit presets stay strict.
_LEGACY_MODEL_PROVIDER_PREFERENCE: list[str] = [
    provider for provider, _ in _PROVIDER_DEFAULT_MODELS
]


def get_default_model() -> str:
    """Return an unambiguous default identifier, upgrading legacy bare names."""
    data = _load_yaml()
    explicit = data.get("default_model", "")
    if explicit:
        if "/" in explicit:
            return explicit
        return _upgrade_bare_default(explicit) or explicit
    for provider_name, bare_name in _PROVIDER_DEFAULT_MODELS:
        if _is_available(provider_name):
            return f"{provider_name}/{bare_name}"
    return ""


def _upgrade_bare_default(bare: str) -> str:
    """Upgrade a bare default using aliases, provider preference, then stable order."""
    aliased = resolve_alias(bare)
    if aliased is not None:
        provider, canonical = aliased
        return f"{provider}/{canonical}"

    all_presets = get_all_presets()
    hits = [prov for (prov, name) in all_presets if name == bare]
    if not hits:
        return ""
    for pref_provider, _ in _PROVIDER_DEFAULT_MODELS:
        if pref_provider in hits:
            return f"{pref_provider}/{bare}"
    return f"{sorted(hits)[0]}/{bare}"


def set_default_model(model_name: str) -> None:
    _save_yaml(
        _serialize_user_data(
            load_presets(), load_backends(), model_name, get_subagent_models()
        )
    )


def save_profile(profile: LLMProfile | LLMPreset) -> None:
    """Persist a provider-scoped preset while preserving existing variations."""
    if isinstance(profile, LLMPreset):
        preset = profile
    else:
        existing_preset = load_presets().get((profile.provider, profile.name))
        preset = LLMPreset(
            name=profile.name,
            model=profile.model,
            provider=profile.provider,
            max_context=profile.max_context,
            max_output=profile.max_output,
            temperature=profile.temperature,
            reasoning_effort=profile.reasoning_effort,
            service_tier=profile.service_tier,
            extra_body=profile.extra_body,
            retry_policy=profile.retry_policy,
            provider_native_tools=(
                deepcopy(existing_preset.provider_native_tools)
                if existing_preset
                else None
            ),
            variation_groups=(
                deepcopy(existing_preset.variation_groups) if existing_preset else {}
            ),
        )

    if not preset.provider:
        raise ValueError("Preset provider is required")

    data = _load_yaml()
    backends = load_backends()
    if preset.provider not in backends:
        raise ValueError(f"Provider not found: {preset.provider}")
    presets = load_presets()
    presets[(preset.provider, preset.name)] = preset
    _save_yaml(
        _serialize_user_data(
            presets,
            backends,
            data.get("default_model", ""),
            get_subagent_models(),
        )
    )


def delete_profile(name: str, provider: str = "") -> bool:
    """Delete one user preset, refusing ambiguous bare-name matches."""
    data = _load_yaml()
    presets = load_presets()
    if provider:
        key = (provider, name)
        if key not in presets:
            return False
        presets.pop(key)
    else:
        hits = [k for k in presets if k[1] == name]
        if len(hits) != 1:
            return False
        presets.pop(hits[0])
    _save_yaml(
        _serialize_user_data(
            presets,
            load_backends(),
            data.get("default_model", ""),
            get_subagent_models(),
        )
    )
    return True


def _builtin_preset_to_runtime(
    provider: str,
    name: str,
    data: dict[str, Any],
    selections: dict[str, str] | None = None,
) -> LLMProfile | None:
    preset = _preset_from_data(name, data, provider)
    return _resolve_preset(preset, load_backends(), selections)


def _all_preset_definitions() -> dict[tuple[str, str], LLMPreset]:
    """Merge user presets over built-ins only on exact provider/name matches."""
    presets: dict[tuple[str, str], LLMPreset] = {}
    for key, data in get_all_presets().items():
        provider, name = key
        presets[key] = _preset_from_data(name, data, provider)
    presets.update(load_presets())
    return presets


def _split_provider_prefix(name: str) -> tuple[str, str]:
    """Split ``provider/name`` into its two parts.

    Returns ``("", name)`` when there is no ``/`` in the input. Empty
    provider or empty name raise ``ValueError`` — callers expect one
    or the other to be non-empty.
    """
    if "/" not in name:
        return "", name
    provider, bare = name.split("/", 1)
    if not provider or not bare:
        raise ValueError(
            f"Invalid provider/name identifier {name!r}: both halves must be non-empty"
        )
    return provider, bare


def _get_preset_definition(name: str, provider: str = "") -> LLMPreset | None:
    """Resolve qualified names, aliases, or uniquely matched bare preset names."""
    base_name, _ = parse_variation_selector(name)
    qualified_provider, bare_name = _split_provider_prefix(base_name)
    if provider:
        qualified_provider = provider

    if not qualified_provider:
        aliased = resolve_alias(bare_name)
        if aliased is not None:
            qualified_provider, bare_name = aliased

    definitions = _all_preset_definitions()

    if qualified_provider:
        preset = definitions.get((qualified_provider, bare_name))
        if preset is not None:
            return preset
        return None

    matches = [p for (prov, n), p in definitions.items() if n == bare_name]
    if not matches:
        return None
    if len(matches) > 1:
        providers = sorted({p.provider or "(none)" for p in matches})
        raise ValueError(
            f"Preset name {bare_name!r} exists under multiple providers: "
            f"{', '.join(providers)}. Use 'provider/name' (e.g. "
            f"'{providers[0]}/{bare_name}') or set controller.provider."
        )
    return matches[0]


def _get_profile_from_selector(
    name: str,
    extra_selections: dict[str, str] | None = None,
    provider: str = "",
) -> LLMProfile | None:
    base_name, selector_selections = parse_variation_selector(name)
    preset = _get_preset_definition(base_name, provider)
    if preset is None:
        return None
    merged_selections = dict(selector_selections)
    merged_selections.update(extra_selections or {})
    return _resolve_preset(preset, load_backends(), merged_selections)


def _find_profile_by_model(
    model: str,
    provider: str = "",
    selections: dict[str, str] | None = None,
) -> LLMProfile | None:
    matches = []
    for preset in _all_preset_definitions().values():
        if preset.model != model:
            continue
        if provider and preset.provider != provider:
            continue
        matches.append(preset)

    if not matches:
        return None
    if len(matches) > 1 and not provider:
        preferred = {preset.provider: preset for preset in matches if preset.provider}
        for preferred_provider in _LEGACY_MODEL_PROVIDER_PREFERENCE:
            chosen = preferred.get(preferred_provider)
            if chosen is not None:
                return _resolve_preset(chosen, load_backends(), selections)
        providers = sorted({preset.provider or "(none)" for preset in matches})
        raise ValueError(
            f"Model '{model}' is ambiguous across multiple providers: {', '.join(providers)}. "
            "Set controller.provider or use a preset name."
        )
    return _resolve_preset(matches[0], load_backends(), selections)


def get_profile(name: str, provider: str = "") -> LLMProfile | None:
    return _get_profile_from_selector(name, provider=provider)


def profile_to_identifier(profile: LLMProfile) -> str:
    """Render a profile as ``provider/name`` plus sorted variation selections."""
    if not profile:
        return ""
    base = f"{profile.provider}/{profile.name}" if profile.provider else profile.name
    selections = profile.selected_variations or {}
    if not selections:
        return base
    parts = [f"{g}={o}" for g, o in sorted(selections.items()) if o]
    if not parts:
        return base
    return f"{base}@" + ",".join(parts)


def get_preset(name: str, provider: str = "") -> LLMProfile | None:
    return _get_profile_from_selector(name, provider=provider)


def _legacy_model_provider_hint(controller_config: dict[str, Any]) -> str:
    """Infer a provider from legacy transport hints without weakening preset checks."""
    auth_mode = controller_config.get("auth_mode", "") or ""
    if auth_mode == "codex-oauth":
        return "codex"
    return ""


def resolve_controller_llm(
    controller_config: dict[str, Any],
    llm: str | None = None,
) -> LLMProfile | None:
    name = llm or controller_config.get("llm")
    raw_model = controller_config.get("model", "")
    provider = controller_config.get("provider", "") or ""

    selection_overrides = dict(controller_config.get("variation_selections") or {})
    legacy_variation = controller_config.get("variation", "")
    if legacy_variation and _SHORTHAND_SELECTION_KEY not in selection_overrides:
        selection_overrides[_SHORTHAND_SELECTION_KEY] = legacy_variation

    profile: LLMProfile | None = None
    if name:
        profile = _get_profile_from_selector(
            name, selection_overrides, provider=provider
        )
    elif raw_model:
        model_name, model_selector_selections = parse_variation_selector(raw_model)
        if model_name:
            merged_selections = dict(model_selector_selections)
            merged_selections.update(selection_overrides)
            model_provider = provider or _legacy_model_provider_hint(controller_config)
            profile = _find_profile_by_model(
                model_name,
                model_provider,
                merged_selections,
            )

    if profile is None and not name and not raw_model:
        default_name = get_default_model()
        if default_name:
            profile = _get_profile_from_selector(default_name, selection_overrides)

    if not profile:
        if name or raw_model:
            logger.warning("LLM profile not found", profile_name=name or raw_model)
        return None

    for key in (
        "temperature",
        "reasoning_effort",
        "service_tier",
        "max_tokens",
        "retry_policy",
    ):
        if key not in controller_config:
            continue
        value = controller_config[key]
        if value is None:
            continue
        if key == "max_tokens":
            profile.max_output = value
        elif key == "retry_policy":
            profile.retry_policy = deepcopy(value)
        else:
            setattr(profile, key, value)

    extra_body = controller_config.get("extra_body") or {}
    if extra_body:
        profile.extra_body = deep_merge_dicts(profile.extra_body or {}, extra_body)

    return profile


def _login_provider_for(profile_or_data: dict[str, Any] | LLMProfile) -> str:
    """Return the provider name a caller should authenticate against."""
    if isinstance(profile_or_data, LLMProfile):
        if profile_or_data.provider:
            return profile_or_data.provider
        return _legacy_provider_from_data(profile_or_data.to_dict())
    return profile_or_data.get("provider", "") or _legacy_provider_from_data(
        profile_or_data
    )


def _is_available(provider_name: str) -> bool:
    if not provider_name:
        return False
    backends = load_backends()
    backend = backends.get(provider_name)
    if backend and backend.backend_type == "codex":
        if backend.base_url:
            # A custom Responses endpoint uses API-key auth instead of OAuth.
            if get_api_key(provider_name):
                return True
            return bool(backend.api_key_env and get_api_key(backend.api_key_env))
        # The built-in Codex endpoint requires a cached OAuth token.
        return CodexTokens.load() is not None
    if provider_name == "codex":
        return CodexTokens.load() is not None
    if backend and backend.backend_type == "grok-subscription":
        return GrokTokens.available()
    if provider_name == "grok-subscription":
        return GrokTokens.available()
    if backend:
        if get_api_key(provider_name):
            return True
        if backend.api_key_env and get_api_key(backend.api_key_env):
            return True
        return False
    if provider_name in PROVIDER_KEY_MAP:
        return bool(get_api_key(provider_name))
    return False


def list_all() -> list[dict[str, Any]]:
    """List resolved presets, with user entries overriding exact built-in keys."""
    result: list[dict[str, Any]] = []
    definitions = _all_preset_definitions()

    def _entry(
        profile: LLMProfile, preset: LLMPreset | None, source: str
    ) -> dict[str, Any]:
        return {
            "name": profile.name,
            "model": profile.model,
            "provider": profile.provider,
            "login_provider": profile.provider,
            "backend_type": profile.backend_type,
            "available": _is_available(profile.provider),
            "source": source,
            "max_context": profile.max_context,
            "max_output": profile.max_output,
            "temperature": profile.temperature,
            "reasoning_effort": profile.reasoning_effort or "",
            "service_tier": profile.service_tier or "",
            "extra_body": profile.extra_body or {},
            "retry_policy": profile.retry_policy,
            "base_url": profile.base_url or "",
            "variation_groups": deepcopy(preset.variation_groups if preset else {}),
            "selected_variations": dict(profile.selected_variations or {}),
        }

    seen: set[tuple[str, str]] = set()
    for (provider, name), preset in load_presets().items():
        profile = _resolve_preset(preset, load_backends())
        if profile is not None:
            seen.add((provider, name))
            result.append(_entry(profile, definitions.get((provider, name)), "user"))

    for (provider, name), data in get_all_presets().items():
        if (provider, name) in seen:
            continue
        profile = _builtin_preset_to_runtime(provider, name, data)
        if profile is None:
            continue
        result.append(_entry(profile, definitions.get((provider, name)), "preset"))

    default = get_default_model()
    default_provider, default_bare = _split_provider_prefix(default)
    for entry in result:
        is_default = False
        if default:
            if default_provider:
                is_default = (
                    entry["provider"] == default_provider
                    and entry["name"] == default_bare
                )
            else:
                is_default = entry["name"] == default or entry["model"] == default
        entry["is_default"] = is_default
    return result
