"""Backend (provider) persistence + the YAML store shared with presets.

The single YAML file ``~/.kohakuterrarium/llm_profiles.yaml`` holds both
custom providers and user presets. This module owns the low-level YAML
read/write and the backend-level CRUD; :mod:`profiles` builds on top for
preset lookup and runtime resolution.

A ``backend_type`` is a tiny enum of transport implementations:
    openai : any OpenAI-compatible ``/chat/completions`` endpoint
             (OpenAI, OpenRouter, Gemini's compat path, Anthropic's compat
             path, MiMo, and user-defined proxies).
    codex  : OpenAI ChatGPT-subscription via OAuth — has its own bespoke
             provider (``CodexOAuthProvider``) in :mod:`codex_provider`.

Legacy ``anthropic`` / ``codex-oauth`` values are silently migrated to
``openai`` / ``codex`` on read — there is no native Anthropic client.

This module intentionally stays read-only + primitive-CRUD so it has no
dependency on :mod:`profiles`. The write-side operations that touch both
backends and presets (``save_backend``, ``delete_backend``, ``save_profile``
etc.) live in :mod:`profiles`.
"""

from typing import Any

import yaml

from kohakuterrarium.llm.api_keys import KT_DIR, PROVIDER_KEY_MAP
from kohakuterrarium.llm.profile_types import LLMBackend
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PROFILES_PATH = KT_DIR / "llm_profiles.yaml"
_SCHEMA_VERSION = 3

_BUILTIN_PROVIDER_NAMES: set[str] = {
    "codex",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "mimo",
}

# Historical values that appeared under a preset's ``provider`` field to
# describe the backend type. They are now only valid as ``backend_type`` and
# get rewritten on load (see ``_normalize_backend_type``).
_LEGACY_BACKEND_TYPE_VALUES: set[str] = {"openai", "codex", "codex-oauth", "anthropic"}


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


def load_yaml_store() -> dict[str, Any]:
    """Read the shared ``llm_profiles.yaml`` — returns ``{}`` on missing/bad file."""
    if not PROFILES_PATH.exists():
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load LLM profiles", error=str(e))
        return {}


def save_yaml_store(data: dict[str, Any]) -> None:
    """Overwrite the shared ``llm_profiles.yaml``."""
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


def legacy_provider_from_data(data: dict[str, Any]) -> str:
    """Best-effort mapping for legacy preset shapes.

    Old presets stored ``provider`` as a backend type (``openai`` /
    ``codex-oauth`` / ``anthropic``) plus ``base_url`` / ``api_key_env``.
    Infer which built-in provider they actually referred to so runtime
    resolution still works after the 2026-04 refactor.
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
    data = load_yaml_store()
    backends = _built_in_providers()

    user_backends = data.get("backends") or data.get("providers") or {}
    if isinstance(user_backends, dict):
        for name, bdata in user_backends.items():
            if isinstance(bdata, dict):
                backends[name] = LLMBackend.from_dict(name, bdata)

    # Legacy fallback: some old profiles stored ``base_url`` / ``api_key_env``
    # inline on each preset. If those map onto a built-in provider that isn't
    # present in the current ``backends`` dict, fabricate a synthetic one so
    # resolution still reaches the right endpoint.
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
    """Return the canonical backend_type for a new/updated provider.

    Raises ``ValueError`` on anything other than ``openai`` / ``codex``
    (post-normalization — ``anthropic`` and ``codex-oauth`` are accepted
    and silently rewritten).
    """
    normalized = _normalize_backend_type(backend_type)
    if normalized not in {"openai", "codex"}:
        raise ValueError(f"Unsupported backend_type: {backend_type}")
    return normalized
