"""LLM preset/provider system — preset loading + runtime resolution.

Backend management lives in :mod:`backends`; the pure variation-selector
machinery lives in :mod:`variations`. This module builds on both for preset
persistence, preset-level YAML round-tripping, and the ``resolve_controller_llm``
entrypoint called from :mod:`bootstrap.llm`.

The two backend types in use:
    openai : OpenAI-compatible HTTP client. Used for OpenAI, OpenRouter,
             Anthropic (via their official OpenAI-compat endpoint at
             ``api.anthropic.com/v1``), Gemini, MiMo, and any user-defined
             provider that exposes a ``/chat/completions`` interface.
    codex  : OpenAI ChatGPT subscription via OAuth.

Note: there is currently no native Anthropic client. The ``anthropic``
built-in provider targets Anthropic's OpenAI-compat endpoint, which accepts
``extra_body.thinking`` (incl. adaptive mode) but silently ignores
top-level ``reasoning_effort`` / ``service_tier`` and fields like
``speed`` / ``betas``. For fast mode or the full native feature set, route
through ``openrouter`` instead.
"""

from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.api_keys import (
    KT_DIR,  # noqa: F401  (re-exported for back-compat)
    KEYS_PATH,  # noqa: F401
    PROVIDER_KEY_MAP,  # noqa: F401
    get_api_key,
    list_api_keys,  # noqa: F401
    save_api_key,
)
from kohakuterrarium.llm.backends import (
    PROFILES_PATH,  # noqa: F401  (re-exported so callers + tests can patch here)
    _BUILTIN_PROVIDER_NAMES,
    _LEGACY_BACKEND_TYPE_VALUES,  # noqa: F401
    _SCHEMA_VERSION,
    _normalize_backend_type,  # noqa: F401
    legacy_provider_from_data as _legacy_provider_from_data,
    load_backends,
    load_yaml_store as _load_yaml,
    save_yaml_store as _save_yaml,
    validate_backend_type,
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.presets import ALIASES, PRESETS, get_all_presets  # noqa: F401
from kohakuterrarium.llm.profile_types import LLMBackend, LLMProfile, LLMPreset
from kohakuterrarium.llm.variations import (
    _SHORTHAND_SELECTION_KEY,
    apply_patch_map,  # noqa: F401
    apply_variation_groups,
    deep_merge_dicts,
    normalize_variation_selections,
    parse_variation_selector,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ── Preset I/O ─────────────────────────────────────────────────


def _preset_from_data(name: str, data: dict[str, Any]) -> LLMPreset:
    """Build a LLMPreset from raw yaml data, inferring provider if legacy."""
    preset = LLMPreset.from_dict(name, data)
    if not preset.provider:
        preset.provider = _legacy_provider_from_data(data)
    return preset


def load_presets() -> dict[str, LLMPreset]:
    data = _load_yaml()
    presets: dict[str, LLMPreset] = {}
    stored = data.get("presets", {})
    if isinstance(stored, dict):
        for name, pdata in stored.items():
            if isinstance(pdata, dict):
                presets[name] = _preset_from_data(name, pdata)
    legacy = data.get("profiles", {})
    if isinstance(legacy, dict):
        for name, pdata in legacy.items():
            if isinstance(pdata, dict) and name not in presets:
                presets[name] = _preset_from_data(name, pdata)
    return presets


def _serialize_user_data(
    presets: dict[str, LLMPreset],
    backends: dict[str, LLMBackend],
    default_model: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {"version": _SCHEMA_VERSION}
    if default_model:
        data["default_model"] = default_model
    user_backends = {
        name: backend.to_dict()
        for name, backend in backends.items()
        if name not in _BUILTIN_PROVIDER_NAMES
    }
    if user_backends:
        data["backends"] = user_backends
    if presets:
        serialized = {name: preset.to_dict() for name, preset in presets.items()}
        data["presets"] = serialized
        data["profiles"] = serialized
    return data


# ── Backend CRUD (writes touch both backends + presets, so lives here) ──


def save_backend(backend: LLMBackend) -> None:
    """Persist a user-defined provider.

    ``backend_type`` values are ``openai`` (any OpenAI-compatible
    ``/chat/completions`` endpoint, including Anthropic's compat layer and
    Gemini's) and ``codex`` (ChatGPT-subscription OAuth). Legacy
    ``anthropic`` / ``codex-oauth`` values are normalized here so older API
    clients keep working.
    """
    backend.backend_type = validate_backend_type(backend.backend_type)
    data = _load_yaml()
    backends = load_backends()
    presets = load_presets()
    backends[backend.name] = backend
    _save_yaml(_serialize_user_data(presets, backends, data.get("default_model", "")))


def delete_backend(name: str) -> bool:
    if name in _BUILTIN_PROVIDER_NAMES:
        raise ValueError(f"Cannot delete built-in provider: {name}")
    data = _load_yaml()
    existing = data.get("backends", {}) or data.get("providers", {})
    if name not in existing:
        return False
    presets = load_presets()
    if any(p.provider == name for p in presets.values()):
        raise ValueError(f"Provider still in use by one or more presets: {name}")
    backends = load_backends()
    backends.pop(name, None)
    _save_yaml(_serialize_user_data(presets, backends, data.get("default_model", "")))
    save_api_key(name, "")
    return True


# ── Runtime resolution ─────────────────────────────────────────


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
        selected_variations=normalized,
    )


def load_profiles() -> dict[str, LLMProfile]:
    backends = load_backends()
    profiles: dict[str, LLMProfile] = {}
    for name, preset in load_presets().items():
        resolved = _resolve_preset(preset, backends)
        if resolved is not None:
            profiles[name] = resolved
    return profiles


_PROVIDER_DEFAULT_MODELS: list[tuple[str, str]] = [
    ("codex", "gpt-5.4"),
    ("openrouter", "mimo-v2-pro-or"),
    ("anthropic", "claude-opus-4.7"),
    ("openai", "gpt-5.4-api"),
    ("gemini", "gemini-3.1-pro"),
    ("mimo", "mimo-v2-pro"),
]


def get_default_model() -> str:
    data = _load_yaml()
    explicit = data.get("default_model", "")
    if explicit:
        return explicit
    for provider_name, model in _PROVIDER_DEFAULT_MODELS:
        if _is_available(provider_name):
            return model
    return ""


def set_default_model(model_name: str) -> None:
    _save_yaml(_serialize_user_data(load_presets(), load_backends(), model_name))


def save_profile(profile: LLMProfile | LLMPreset) -> None:
    """Persist a user-defined preset.

    When called with an :class:`LLMProfile` (which has no ``variation_groups``
    field of its own), any ``variation_groups`` already defined on the existing
    preset of the same name are preserved — otherwise round-tripping a profile
    through the API would silently erase its variation set.
    """
    if isinstance(profile, LLMPreset):
        preset = profile
    else:
        existing_preset = load_presets().get(profile.name)
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
    presets[preset.name] = preset
    _save_yaml(_serialize_user_data(presets, backends, data.get("default_model", "")))


def delete_profile(name: str) -> bool:
    data = _load_yaml()
    presets = load_presets()
    if name not in presets:
        return False
    presets.pop(name)
    _save_yaml(
        _serialize_user_data(presets, load_backends(), data.get("default_model", ""))
    )
    return True


def _builtin_preset_to_runtime(
    name: str,
    data: dict[str, Any],
    selections: dict[str, str] | None = None,
) -> LLMProfile | None:
    preset = _preset_from_data(name, data)
    return _resolve_preset(preset, load_backends(), selections)


def _all_preset_definitions() -> dict[str, LLMPreset]:
    presets = load_presets()
    for name, data in get_all_presets().items():
        if name not in presets:
            presets[name] = _preset_from_data(name, data)
    return presets


def _get_preset_definition(name: str) -> LLMPreset | None:
    base_name, _ = parse_variation_selector(name)
    canonical = ALIASES.get(base_name, base_name)

    user_presets = load_presets()
    if canonical in user_presets:
        return user_presets[canonical]
    if base_name in user_presets:
        return user_presets[base_name]

    presets = get_all_presets()
    if canonical in presets:
        return _preset_from_data(canonical, presets[canonical])
    if base_name in presets:
        return _preset_from_data(base_name, presets[base_name])
    return None


def _get_profile_from_selector(
    name: str,
    extra_selections: dict[str, str] | None = None,
) -> LLMProfile | None:
    base_name, selector_selections = parse_variation_selector(name)
    preset = _get_preset_definition(base_name)
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
        providers = sorted({preset.provider or "(none)" for preset in matches})
        raise ValueError(
            f"Model '{model}' is ambiguous across multiple providers: {', '.join(providers)}. "
            "Set controller.provider or use a preset name."
        )
    return _resolve_preset(matches[0], load_backends(), selections)


def get_profile(name: str) -> LLMProfile | None:
    return _get_profile_from_selector(name)


def get_preset(name: str) -> LLMProfile | None:
    return _get_profile_from_selector(name)


def resolve_controller_llm(
    controller_config: dict[str, Any],
    llm_override: str | None = None,
) -> LLMProfile | None:
    name = llm_override or controller_config.get("llm")
    raw_model = controller_config.get("model", "")
    provider = controller_config.get("provider", "") or ""

    selection_overrides = dict(controller_config.get("variation_selections") or {})
    legacy_variation = controller_config.get("variation", "")
    if legacy_variation and _SHORTHAND_SELECTION_KEY not in selection_overrides:
        selection_overrides[_SHORTHAND_SELECTION_KEY] = legacy_variation

    profile: LLMProfile | None = None
    if name:
        profile = _get_profile_from_selector(name, selection_overrides)
    elif raw_model:
        model_name, model_selector_selections = parse_variation_selector(raw_model)
        if model_name:
            merged_selections = dict(model_selector_selections)
            merged_selections.update(selection_overrides)
            profile = _find_profile_by_model(model_name, provider, merged_selections)

    if profile is None and not name and not raw_model:
        default_name = get_default_model()
        if default_name:
            profile = _get_profile_from_selector(default_name, selection_overrides)

    if not profile:
        if name or raw_model:
            logger.warning("LLM profile not found", profile_name=name or raw_model)
        return None

    for key in ("temperature", "reasoning_effort", "service_tier", "max_tokens"):
        if key not in controller_config:
            continue
        value = controller_config[key]
        if value is None:
            continue
        if key == "max_tokens":
            profile.max_output = value
        else:
            setattr(profile, key, value)

    extra_body = controller_config.get("extra_body") or {}
    if extra_body:
        profile.extra_body = deep_merge_dicts(profile.extra_body or {}, extra_body)

    return profile


# ── Helpers ────────────────────────────────────────────────────


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
        return CodexTokens.load() is not None
    if provider_name == "codex":
        return CodexTokens.load() is not None
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
    """List every user + built-in preset resolved against current providers."""
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
            "base_url": profile.base_url or "",
            "variation_groups": deepcopy(preset.variation_groups if preset else {}),
            "selected_variations": dict(profile.selected_variations or {}),
        }

    for name, preset in load_presets().items():
        profile = _resolve_preset(preset, load_backends())
        if profile is not None:
            result.append(_entry(profile, definitions.get(name), "user"))

    user_names = {entry["name"] for entry in result}
    for name, data in get_all_presets().items():
        if name in user_names:
            continue
        profile = _builtin_preset_to_runtime(name, data)
        if profile is None:
            continue
        result.append(_entry(profile, definitions.get(name), "preset"))

    default = get_default_model()
    for entry in result:
        entry["is_default"] = entry["name"] == default or entry["model"] == default
    return result
