"""LLM preset/provider system.

Two-layer model:
    preset  -> references a provider by name (and owns model-facing params)
    provider -> concrete transport binding (backend_type + base_url + api_key_env)

Backend types are a small enum of implementations:
    openai : OpenAI-compatible HTTP client. Used for OpenAI, OpenRouter,
             Anthropic (via their official OpenAI-compat endpoint at
             ``api.anthropic.com/v1``), Gemini, MiMo, and any user-defined
             provider that exposes a ``/chat/completions`` interface.
    codex  : OpenAI ChatGPT subscription via OAuth.

Built-in providers cover the common cases (codex, openai, openrouter,
anthropic, gemini, mimo). Users can add custom providers in
``~/.kohakuterrarium/llm_profiles.yaml`` and custom presets that reference
any provider (built-in or custom).

Note: there is currently no native Anthropic client. The ``anthropic``
built-in provider targets Anthropic's OpenAI-compat endpoint, which accepts
``extra_body.thinking`` (incl. adaptive mode) but silently ignores
top-level ``reasoning_effort`` / ``service_tier`` and fields like
``speed`` / ``betas``. For fast mode or the full native feature set, route
through ``openrouter`` instead.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import yaml

from kohakuterrarium.llm.api_keys import (
    KT_DIR,
    KEYS_PATH,  # noqa: F401
    PROVIDER_KEY_MAP,
    get_api_key,
    list_api_keys,  # noqa: F401
    save_api_key,  # noqa: F401
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.presets import ALIASES, PRESETS, get_all_presets  # noqa: F401
from kohakuterrarium.llm.profile_types import LLMBackend, LLMProfile, LLMPreset
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PROFILES_PATH = KT_DIR / "llm_profiles.yaml"
_SCHEMA_VERSION = 3
_BUILTIN_PROVIDER_NAMES = {
    "codex",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "mimo",
}

# Values that historically appeared under a preset's `provider` field to
# describe the backend type. They are now only valid as `backend_type`.
_LEGACY_BACKEND_TYPE_VALUES = {"openai", "codex", "codex-oauth", "anthropic"}
_ALLOWED_VARIATION_ROOTS = {
    "temperature",
    "reasoning_effort",
    "service_tier",
    "max_context",
    "max_output",
    "extra_body",
}
_SHORTHAND_SELECTION_KEY = "__option__"


def _normalize_backend_type(value: str) -> str:
    """Map legacy / user-typed backend types onto the current canonical set.

    - ``"codex-oauth"`` → ``"codex"`` (old name for the ChatGPT-OAuth backend)
    - ``"anthropic"`` → ``"openai"`` (there is no native Anthropic client;
      the anthropic *provider* now points at Anthropic's OpenAI-compat
      endpoint and speaks ``/chat/completions``). Legacy profiles that
      declare ``backend_type: anthropic`` are auto-migrated here.
    - empty / unknown → ``"openai"`` (safe default for unconfigured data).
    """
    if value == "codex-oauth":
        return "codex"
    if value == "anthropic":
        return "openai"
    return value or "openai"


def _load_yaml() -> dict[str, Any]:
    if not PROFILES_PATH.exists():
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load LLM profiles", error=str(e))
        return {}


def _save_yaml(data: dict[str, Any]) -> None:
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _built_in_providers() -> dict[str, LLMBackend]:
    return {
        "codex": LLMBackend(name="codex", backend_type="codex"),
        "openai": LLMBackend(
            name="openai",
            backend_type="openai",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        ),
        "openrouter": LLMBackend(
            name="openrouter",
            backend_type="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        ),
        # Anthropic's OpenAI-compatible endpoint. No native Anthropic client
        # in this project — we speak /chat/completions and pass Claude-specific
        # knobs (``thinking: {type: "adaptive"}``, ``thinking.budget_tokens``)
        # through ``extra_body``. Top-level ``reasoning_effort`` / ``service_tier``
        # and ``speed`` / ``betas`` fields are silently dropped by the compat
        # layer; for those, use the ``openrouter`` provider instead.
        "anthropic": LLMBackend(
            name="anthropic",
            backend_type="openai",
            base_url="https://api.anthropic.com/v1/",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        "gemini": LLMBackend(
            name="gemini",
            backend_type="openai",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key_env="GEMINI_API_KEY",
        ),
        "mimo": LLMBackend(
            name="mimo",
            backend_type="openai",
            base_url="https://api.xiaomimimo.com/v1",
            api_key_env="MIMO_API_KEY",
        ),
    }


def _legacy_provider_from_data(data: dict[str, Any]) -> str:
    """Best-effort mapping for legacy preset shapes.

    Old presets stored ``provider`` as a backend type (``openai``/``codex-oauth``
    /``anthropic``) plus ``base_url``/``api_key_env``. Infer which built-in
    provider they actually referred to so the runtime resolution still works.
    """
    value = data.get("provider", "")
    if value and value not in _LEGACY_BACKEND_TYPE_VALUES:
        return value

    # Raw (un-normalized) backend_type declaration — ``anthropic`` here is a
    # legacy signal that the preset was targeting Anthropic direct, which
    # now resolves to the built-in ``anthropic`` provider (backend_type=openai).
    raw_backend_type = data.get("backend_type") or data.get("provider", "openai")
    backend_type = _normalize_backend_type(raw_backend_type)
    base_url = data.get("base_url", "")
    api_key_env = data.get("api_key_env", "")

    if backend_type == "codex":
        return "codex"
    if raw_backend_type == "anthropic" or "api.anthropic.com" in base_url:
        return "anthropic"
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "generativelanguage.googleapis.com" in base_url:
        return "gemini"
    if "api.openai.com" in base_url:
        return "openai"
    if "mimo" in base_url:
        return "mimo"
    if api_key_env in {
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "MIMO_API_KEY",
    }:
        reverse = {v: k for k, v in PROVIDER_KEY_MAP.items()}
        return reverse[api_key_env]
    return ""


def load_backends() -> dict[str, LLMBackend]:
    """Return merged built-in + user-defined providers."""
    data = _load_yaml()
    backends = _built_in_providers()

    user_backends = data.get("backends") or data.get("providers") or {}
    if isinstance(user_backends, dict):
        for name, bdata in user_backends.items():
            if isinstance(bdata, dict):
                backends[name] = LLMBackend.from_dict(name, bdata)

    legacy = data.get("profiles", {})
    if isinstance(legacy, dict):
        for name, pdata in legacy.items():
            if not isinstance(pdata, dict):
                continue
            inferred = _legacy_provider_from_data(pdata)
            if inferred and inferred not in backends:
                backends[inferred] = LLMBackend(
                    name=inferred,
                    backend_type=_normalize_backend_type(
                        pdata.get("backend_type") or pdata.get("provider", "openai")
                    ),
                    base_url=pdata.get("base_url", ""),
                    api_key_env=pdata.get("api_key_env", ""),
                )
    return backends


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


def save_backend(backend: LLMBackend) -> None:
    """Persist a user-defined provider.

    ``backend_type`` values are ``openai`` (any OpenAI-compatible
    ``/chat/completions`` endpoint, including Anthropic's compat layer and
    Gemini's) and ``codex`` (ChatGPT-subscription OAuth). Legacy
    ``anthropic`` / ``codex-oauth`` values are normalized at read time and
    should not be supplied here.
    """
    backend.backend_type = _normalize_backend_type(backend.backend_type)
    if backend.backend_type not in {"openai", "codex"}:
        raise ValueError(f"Unsupported backend_type: {backend.backend_type}")
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


def parse_variation_selector(selector: str) -> tuple[str, dict[str, str]]:
    """Parse ``preset@group=option,group2=option2`` into name + selections."""
    if "@" not in selector:
        return selector, {}

    base_name, raw_selector = selector.split("@", 1)
    if not base_name:
        raise ValueError("Variation selector is missing a preset/model name before '@'")
    if not raw_selector.strip():
        raise ValueError(f"Variation selector for '{base_name}' is empty")

    selections: dict[str, str] = {}
    for raw_part in raw_selector.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"Invalid empty variation selection in '{selector}'")
        if "=" in part:
            group, option = part.split("=", 1)
            group = group.strip()
            option = option.strip()
            if not group or not option:
                raise ValueError(
                    f"Invalid variation selection '{part}' in '{selector}'"
                )
            selections[group] = option
        else:
            if _SHORTHAND_SELECTION_KEY in selections:
                raise ValueError(
                    "Variation shorthand may only specify one option without a group"
                )
            selections[_SHORTHAND_SELECTION_KEY] = part
    return base_name, selections


def _validate_patch_target(path: str) -> None:
    root = path.split(".", 1)[0]
    if root not in _ALLOWED_VARIATION_ROOTS:
        raise ValueError(
            f"Unsupported variation patch target '{path}'. "
            f"Allowed roots: {', '.join(sorted(_ALLOWED_VARIATION_ROOTS))}"
        )


def _set_dotted_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        existing = cur.get(part)
        if existing is None:
            existing = {}
            cur[part] = existing
        if not isinstance(existing, dict):
            raise ValueError(
                f"Cannot apply variation patch '{path}': '{part}' is not an object"
            )
        cur = existing
    cur[parts[-1]] = deepcopy(value)


def apply_patch_map(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for path, value in (patch or {}).items():
        _validate_patch_target(path)
        _set_dotted_path(result, path, value)
    return result


def normalize_variation_selections(
    selection_map: dict[str, str],
    preset: LLMPreset,
) -> dict[str, str]:
    """Resolve shorthand selections and validate groups/options."""
    groups = preset.variation_groups or {}
    selections = dict(selection_map or {})
    normalized: dict[str, str] = {}

    shorthand = selections.pop(_SHORTHAND_SELECTION_KEY, "")
    if shorthand:
        matching_groups = [
            group_name
            for group_name, options in groups.items()
            if shorthand in (options or {})
        ]
        if not matching_groups:
            raise ValueError(
                f"Unknown variation option '{shorthand}' for preset '{preset.name}'"
            )
        if len(matching_groups) > 1:
            raise ValueError(
                f"Ambiguous variation option '{shorthand}' for preset '{preset.name}'. "
                f"Specify one of: {', '.join(f'{g}={shorthand}' for g in matching_groups)}"
            )
        normalized[matching_groups[0]] = shorthand

    for group_name, option_name in selections.items():
        if group_name not in groups:
            raise ValueError(
                f"Unknown variation group '{group_name}' for preset '{preset.name}'"
            )
        group_options = groups[group_name] or {}
        if option_name not in group_options:
            raise ValueError(
                f"Unknown variation option '{option_name}' in group '{group_name}' "
                f"for preset '{preset.name}'"
            )
        normalized[group_name] = option_name

    return normalized


def apply_variation_groups(
    base: dict[str, Any],
    variation_groups: dict[str, dict[str, dict[str, Any]]],
    selections: dict[str, str],
) -> dict[str, Any]:
    result = deepcopy(base)
    written_paths: dict[str, tuple[str, str]] = {}

    for group_name, option_name in selections.items():
        patch = ((variation_groups or {}).get(group_name) or {}).get(option_name) or {}
        for path in patch:
            _validate_patch_target(path)
            prior = written_paths.get(path)
            if prior is not None:
                prev_group, prev_option = prior
                raise ValueError(
                    f"Variation selections conflict on '{path}': "
                    f"{prev_group}={prev_option} and {group_name}={option_name}"
                )
            written_paths[path] = (group_name, option_name)
        result = apply_patch_map(result, patch)

    return result


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


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
    ("openrouter", "mimo-v2-pro"),
    ("anthropic", "claude-opus-4.6-direct"),
    ("openai", "gpt-5.4-direct"),
    ("gemini", "gemini-3.1-pro-direct"),
    ("mimo", "mimo-v2-pro-direct"),
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
        profile.extra_body = _deep_merge_dicts(profile.extra_body or {}, extra_body)

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
