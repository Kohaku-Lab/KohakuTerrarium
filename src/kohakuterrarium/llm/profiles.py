"""LLM preset/provider system.

Two-layer model:
    preset  -> references a provider by name (and owns model-facing params)
    provider -> concrete transport binding (backend_type + base_url + api_key_env)

Backend types are a small enum of implementations:
    openai    : OpenAI-compatible HTTP client
    codex     : OpenAI ChatGPT subscription via OAuth
    anthropic : Anthropic-native client (dedicated thinking API)

Built-in providers cover the common cases (codex, openai, openrouter,
anthropic, gemini, mimo). Users can add custom providers in
``~/.kohakuterrarium/llm_profiles.yaml`` and custom presets that reference
any provider (built-in or custom).
"""

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


def _normalize_backend_type(value: str) -> str:
    if value == "codex-oauth":
        return "codex"
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
        "anthropic": LLMBackend(
            name="anthropic",
            backend_type="anthropic",
            base_url="https://api.anthropic.com/v1",
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
        return value  # already a real provider name

    backend_type = _normalize_backend_type(
        data.get("backend_type") or data.get("provider", "openai")
    )
    base_url = data.get("base_url", "")
    api_key_env = data.get("api_key_env", "")
    if backend_type == "codex":
        return "codex"
    if backend_type == "anthropic":
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

    # Legacy fallback: old profiles stored base_url/api_key_env inline on each
    # preset. Reconstruct synthetic providers from any unseen (base_url,
    # api_key_env, backend_type) tuple so those presets keep working.
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
        # Keep a ``profiles`` mirror for legacy tooling that reads the old key.
        data["profiles"] = serialized
    return data


def save_backend(backend: LLMBackend) -> None:
    """Persist a user-defined provider."""
    if backend.backend_type not in {"openai", "codex", "anthropic"}:
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


def _resolve_preset(
    preset: LLMPreset, backends: dict[str, LLMBackend]
) -> LLMProfile | None:
    provider = backends.get(preset.provider) if preset.provider else None
    if preset.provider and provider is None:
        return None
    return LLMProfile(
        name=preset.name,
        model=preset.model,
        provider=preset.provider,
        backend_type=provider.backend_type if provider else "",
        max_context=preset.max_context,
        max_output=preset.max_output,
        base_url=provider.base_url if provider else "",
        api_key_env=provider.api_key_env if provider else "",
        temperature=preset.temperature,
        reasoning_effort=preset.reasoning_effort,
        service_tier=preset.service_tier,
        extra_body=preset.extra_body,
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


def save_profile(profile: LLMProfile) -> None:
    """Persist a user-defined preset.

    `LLMProfile` doubles as the user-facing input form — only a handful of
    fields (name, model, provider, params) flow into the saved preset; the
    rest (backend_type, base_url, api_key_env) come from the provider.
    """
    if not profile.provider:
        raise ValueError("Preset provider is required")
    data = _load_yaml()
    backends = load_backends()
    if profile.provider not in backends:
        raise ValueError(f"Provider not found: {profile.provider}")
    presets = load_presets()
    presets[profile.name] = LLMPreset(
        name=profile.name,
        model=profile.model,
        provider=profile.provider,
        max_context=profile.max_context,
        max_output=profile.max_output,
        temperature=profile.temperature,
        reasoning_effort=profile.reasoning_effort,
        service_tier=profile.service_tier,
        extra_body=profile.extra_body,
    )
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


def _builtin_preset_to_runtime(name: str, data: dict[str, Any]) -> LLMProfile | None:
    """Turn a built-in preset dict into a resolved ``LLMProfile``."""
    preset = _preset_from_data(name, data)
    return _resolve_preset(preset, load_backends())


def get_profile(name: str) -> LLMProfile | None:
    canonical = ALIASES.get(name, name)
    profiles = load_profiles()
    if canonical in profiles:
        return profiles[canonical]
    presets = get_all_presets()
    if canonical in presets:
        return _builtin_preset_to_runtime(canonical, presets[canonical])
    if name in presets:
        return _builtin_preset_to_runtime(name, presets[name])
    return None


def get_preset(name: str) -> LLMProfile | None:
    canonical = ALIASES.get(name, name)
    presets = get_all_presets()
    if canonical in presets:
        return _builtin_preset_to_runtime(canonical, presets[canonical])
    return None


def resolve_controller_llm(
    controller_config: dict[str, Any],
    llm_override: str | None = None,
) -> LLMProfile | None:
    name = llm_override or controller_config.get("llm")
    if not name and not controller_config.get("model", ""):
        name = get_default_model()
    if not name:
        return None
    profile = get_profile(name)
    if not profile:
        logger.warning("LLM profile not found", profile_name=name)
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

    def _entry(profile: LLMProfile, source: str) -> dict[str, Any]:
        return {
            "name": profile.name,
            "model": profile.model,
            "provider": profile.provider,
            "login_provider": profile.provider,  # backward-compat alias
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
        }

    for name, profile in load_profiles().items():
        result.append(_entry(profile, "user"))
    user_names = {entry["name"] for entry in result}
    for name, data in get_all_presets().items():
        if name in user_names:
            continue
        profile = _builtin_preset_to_runtime(name, data)
        if profile is None:
            continue
        result.append(_entry(profile, "preset"))
    default = get_default_model()
    for entry in result:
        entry["is_default"] = entry["name"] == default or entry["model"] == default
    return result
