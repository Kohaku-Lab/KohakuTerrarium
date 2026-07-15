"""Backend (provider) persistence + the YAML store shared with presets.

Load provider backends and the shared LLM profile YAML store.

Backend types select OpenAI-compatible, Anthropic-compatible, or Codex OAuth
transport. Legacy ``codex-oauth`` values normalize to ``codex``.
"""

from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.llm.api_keys import KT_DIR, PROVIDER_KEY_MAP
from kohakuterrarium.llm.profile_types import LLMBackend
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Preserve the display constant while live I/O resolves the current config root.
PROFILES_PATH = KT_DIR / "llm_profiles.yaml"
_SCHEMA_VERSION = 3


def _profiles_path() -> Path:
    """Resolve the profile store against the current configuration root."""
    return config_dir() / "llm_profiles.yaml"


_BUILTIN_PROVIDER_NAMES: set[str] = {
    "codex",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "mimo",
    "kimi-code",
    "glm-coding",
}

# Legacy presets sometimes placed transport types in the provider field.
_LEGACY_BACKEND_TYPE_VALUES: set[str] = {"openai", "codex", "codex-oauth", "anthropic"}


def _normalize_backend_type(value: str) -> str:
    """Normalize legacy transport names and default empty values to OpenAI."""
    if value == "codex-oauth":
        return "codex"
    return value or "openai"


def load_yaml_store() -> dict[str, Any]:
    """Read the shared ``llm_profiles.yaml`` — returns ``{}`` on missing/bad file."""
    path = _profiles_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load LLM profiles", error=str(e))
        return {}


def save_yaml_store(data: dict[str, Any]) -> None:
    """Overwrite the shared ``llm_profiles.yaml``."""
    path = _profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _built_in_providers() -> dict[str, LLMBackend]:
    """Return built-in provider transports and native-tool compatibility metadata."""
    return {
        "codex": LLMBackend(
            name="codex",
            backend_type="codex",
            provider_name="codex",
            provider_native_tools=["image_gen"],
        ),
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
            base_url="https://api.anthropic.com",
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
        "kimi-code": LLMBackend(
            name="kimi-code",
            backend_type="anthropic",
            base_url="https://api.kimi.com/coding/",
            api_key_env="KIMI_CODE_API_KEY",
        ),
        "glm-coding": LLMBackend(
            name="glm-coding",
            backend_type="anthropic",
            base_url="https://open.bigmodel.cn/api/anthropic",
            api_key_env="GLM_CODING_API_KEY",
        ),
    }


def legacy_provider_from_data(data: dict[str, Any]) -> str:
    """Infer a built-in provider from legacy transport and endpoint fields."""
    value = data.get("provider", "")
    if value and value not in _LEGACY_BACKEND_TYPE_VALUES:
        return value

    raw_backend_type = data.get("backend_type") or data.get("provider", "openai")
    backend_type = _normalize_backend_type(raw_backend_type)
    base_url = data.get("base_url", "")
    api_key_env = data.get("api_key_env", "")

    if backend_type == "codex":
        return "codex"
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "generativelanguage.googleapis.com" in base_url:
        return "gemini"
    if "api.openai.com" in base_url:
        return "openai"
    if "mimo" in base_url:
        return "mimo"
    if "api.kimi.com/coding" in base_url:
        return "kimi-code"
    if "open.bigmodel.cn/api/anthropic" in base_url:
        return "glm-coding"
    if raw_backend_type == "anthropic" or "api.anthropic.com" in base_url:
        return "anthropic"
    if api_key_env in {
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "MIMO_API_KEY",
        "KIMI_CODE_API_KEY",
        "GLM_CODING_API_KEY",
    }:
        reverse = {v: k for k, v in PROVIDER_KEY_MAP.items()}
        return reverse[api_key_env]
    return ""


_remote_backends: dict[str, LLMBackend] = {}


def set_remote_backend(backend: LLMBackend) -> None:
    """Cache a host-resolved backend so worker-side synchronous lookup can resolve it."""
    _remote_backends[backend.name] = backend


def clear_remote_backends() -> None:
    """Invalidate all host-resolved backend entries."""
    _remote_backends.clear()


def load_backends() -> dict[str, LLMBackend]:
    """Merge providers while preserving environment templates for consume-time expansion."""
    data = load_yaml_store()
    backends = _built_in_providers()

    user_backends = data.get("backends") or data.get("providers") or {}
    if isinstance(user_backends, dict):
        for name, bdata in user_backends.items():
            if isinstance(bdata, dict):
                backends[name] = LLMBackend.from_dict(name, bdata)

    # Local definitions override host-fetched entries with the same name.
    for name, backend in _remote_backends.items():
        backends.setdefault(name, backend)

    # Custom providers default their native-tool compatibility key to their own name.
    for name, backend in backends.items():
        if name not in _BUILTIN_PROVIDER_NAMES and not backend.provider_name:
            backend.provider_name = name

    # Synthesize missing providers for legacy presets with inline transport fields.
    legacy = data.get("profiles", {})
    if isinstance(legacy, dict):
        for _name, pdata in legacy.items():
            if not isinstance(pdata, dict):
                continue
            inferred = legacy_provider_from_data(pdata)
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


def validate_backend_type(backend_type: str) -> str:
    """Validate and return a canonical provider transport type."""
    normalized = _normalize_backend_type(backend_type)
    if normalized not in {"openai", "anthropic", "codex"}:
        raise ValueError(f"Unsupported backend_type: {backend_type}")
    return normalized
