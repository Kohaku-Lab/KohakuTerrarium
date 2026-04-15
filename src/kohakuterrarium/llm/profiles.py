"""
LLM profile system: centralized model configuration.

Profiles define complete LLM settings (provider, model, context limits,
extra params). Stored in ~/.kohakuterrarium/llm_profiles.yaml.

Resolution order for an agent's LLM:
  1. controller.llm (profile name) in agent config
  2. default_model in ~/.kohakuterrarium/llm_profiles.yaml
  3. Inline controller config (backward compat)
  4. Built-in presets by model name

Built-in presets include model-specific metadata (context size, output
limits, required extra_body params) that can't be obtained from APIs.
"""

from dataclasses import dataclass, field
from typing import Any

import yaml

from kohakuterrarium.llm.api_keys import (
    KT_DIR,
    KEYS_PATH,  # noqa: F401  (re-export)
    PROVIDER_KEY_MAP,
    get_api_key,
    list_api_keys,  # noqa: F401  (re-export)
    save_api_key,  # noqa: F401  (re-export)
)
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.presets import ALIASES, PRESETS
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PROFILES_PATH = KT_DIR / "llm_profiles.yaml"

# ── Profile dataclass ─────────────────────────────────────────


@dataclass
class LLMProfile:
    """A complete LLM configuration."""

    name: str
    provider: str  # "codex-oauth" | "openai"
    model: str
    max_context: int = 256000
    max_output: int = 65536
    base_url: str = ""
    api_key_env: str = ""
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
        if self.base_url:
            d["base_url"] = self.base_url
        if self.api_key_env:
            d["api_key_env"] = self.api_key_env
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.reasoning_effort:
            d["reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            d["service_tier"] = self.service_tier
        if self.extra_body:
            d["extra_body"] = self.extra_body
        return d

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "LLMProfile":
        return cls(
            name=name,
            provider=data.get("provider", "openai"),
            model=data.get("model", ""),
            max_context=data.get("max_context", 256000),
            max_output=data.get("max_output", 65536),
            base_url=data.get("base_url", ""),
            api_key_env=data.get("api_key_env", ""),
            temperature=data.get("temperature"),
            reasoning_effort=data.get("reasoning_effort", ""),
            service_tier=data.get("service_tier", ""),
            extra_body=data.get("extra_body", {}),
        )


# ── Profile storage ───────────────────────────────────────────


def _load_yaml() -> dict[str, Any]:
    """Load the profiles YAML file."""
    if not PROFILES_PATH.exists():
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load LLM profiles", error=str(e))
        return {}


def _save_yaml(data: dict[str, Any]) -> None:
    """Save the profiles YAML file."""
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_profiles() -> dict[str, LLMProfile]:
    """Load all user-defined profiles."""
    data = _load_yaml()
    profiles = {}
    for name, pdata in data.get("profiles", {}).items():
        if isinstance(pdata, dict):
            profiles[name] = LLMProfile.from_dict(name, pdata)
    return profiles


# Provider priority + best model per provider for auto-default
_PROVIDER_DEFAULT_MODELS: list[tuple[str, str]] = [
    ("codex", "gpt-5.4"),
    ("openrouter", "mimo-v2-pro"),
    ("anthropic", "claude-opus-4.6-direct"),
    ("openai", "gpt-5.4-direct"),
    ("gemini", "gemini-3.1-pro-direct"),
    ("mimo", "mimo-v2-pro-direct"),
]


def get_default_model() -> str:
    """Get the default model name.

    Resolution order:
      1. Explicit user setting (``kt model default <name>``)
      2. Auto-detect from available API keys (priority: codex > anthropic >
         openai > gemini > openrouter > mimo)
    """
    data = _load_yaml()
    explicit = data.get("default_model", "")
    if explicit:
        return explicit

    # Auto-detect from available keys
    for provider, model in _PROVIDER_DEFAULT_MODELS:
        if _is_available(provider):
            return model
    return ""


def set_default_model(model_name: str) -> None:
    """Set the default model name."""
    data = _load_yaml()
    data["default_model"] = model_name
    _save_yaml(data)
    logger.info("Default model set", model=model_name)


def save_profile(profile: LLMProfile) -> None:
    """Save a user-defined profile."""
    data = _load_yaml()
    if "profiles" not in data:
        data["profiles"] = {}
    data["profiles"][profile.name] = profile.to_dict()
    _save_yaml(data)
    logger.info("Profile saved", profile=profile.name)


def delete_profile(name: str) -> bool:
    """Delete a user-defined profile. Returns True if found."""
    data = _load_yaml()
    profiles = data.get("profiles", {})
    if name in profiles:
        del profiles[name]
        _save_yaml(data)
        return True
    return False


# ── Profile resolution ────────────────────────────────────────


def get_profile(name: str) -> LLMProfile | None:
    """Look up a profile by name.

    Resolution: user profiles -> aliases -> presets.
    """
    # Resolve alias
    canonical = ALIASES.get(name, name)

    # User profiles first
    profiles = load_profiles()
    if canonical in profiles:
        return profiles[canonical]

    # Built-in presets
    if canonical in PRESETS:
        return LLMProfile.from_dict(canonical, PRESETS[canonical])

    # Try original name in presets (in case alias didn't match)
    if name in PRESETS:
        return LLMProfile.from_dict(name, PRESETS[name])

    return None


def get_preset(name: str) -> LLMProfile | None:
    """Look up a built-in preset only (not user profiles)."""
    canonical = ALIASES.get(name, name)
    if canonical in PRESETS:
        return LLMProfile.from_dict(canonical, PRESETS[canonical])
    return None


def resolve_controller_llm(
    controller_config: dict[str, Any],
    llm_override: str | None = None,
) -> LLMProfile | None:
    """Resolve the LLM profile for a controller config.

    Resolution order:
      1. llm_override (from --llm CLI flag)
      2. controller_config["llm"] (profile name in agent config)
      3. default_model from ~/.kohakuterrarium/llm_profiles.yaml
         (only if agent has no explicit inline model)
      4. None (fall back to inline controller config, backward compat)

    Returns None if no profile found (caller should use inline config).
    """
    # 1. CLI override
    name = llm_override

    # 2. Config reference
    if not name:
        name = controller_config.get("llm")

    # 3. Default model (only when agent didn't set an explicit inline model)
    if not name:
        inline_model = controller_config.get("model", "")
        if not inline_model:
            name = get_default_model()

    if not name:
        return None

    profile = get_profile(name)
    if not profile:
        logger.warning("LLM profile not found", profile_name=name)
        return None

    # Merge inline overrides from controller config
    overrides = {}
    for key in ("temperature", "reasoning_effort", "service_tier", "max_tokens"):
        if key in controller_config and key != "llm":
            if key == "max_tokens":
                overrides["max_output"] = controller_config[key]
            else:
                overrides[key] = controller_config[key]

    if overrides:
        for k, v in overrides.items():
            if hasattr(profile, k) and v is not None:
                setattr(profile, k, v)

    return profile


def _login_provider_for(profile_or_data: dict[str, Any] | LLMProfile) -> str:
    """Determine which ``kt login <provider>`` gives access to this model.

    Returns the login provider name (codex, openrouter, openai, anthropic,
    gemini, mimo) or empty string if unknown.
    """
    if isinstance(profile_or_data, LLMProfile):
        provider = profile_or_data.provider
        api_key_env = profile_or_data.api_key_env
    else:
        provider = profile_or_data.get("provider", "")
        api_key_env = profile_or_data.get("api_key_env", "")

    if provider == "codex-oauth":
        return "codex"

    # Reverse lookup: env var -> login provider
    _ENV_TO_LOGIN = {v: k for k, v in PROVIDER_KEY_MAP.items()}
    if api_key_env in _ENV_TO_LOGIN:
        return _ENV_TO_LOGIN[api_key_env]

    return provider


def _is_available(login_provider: str) -> bool:
    """Check if credentials exist for a login provider."""
    if login_provider == "codex":
        return CodexTokens.load() is not None
    if login_provider in PROVIDER_KEY_MAP:
        return bool(get_api_key(login_provider))
    return False


def list_all() -> list[dict[str, Any]]:
    """List all profiles and presets with availability info."""
    result = []

    # User profiles
    for name, profile in load_profiles().items():
        login = _login_provider_for(profile)
        result.append(
            {
                "name": name,
                "model": profile.model,
                "provider": profile.provider,
                "login_provider": login,
                "available": _is_available(login),
                "source": "user",
                "max_context": profile.max_context,
                "max_output": profile.max_output,
                "temperature": profile.temperature,
                "reasoning_effort": profile.reasoning_effort or "",
                "extra_body": profile.extra_body or {},
                "base_url": profile.base_url or "",
            }
        )

    # Presets (skip if user has same name)
    user_names = {r["name"] for r in result}
    for name, data in PRESETS.items():
        if name not in user_names:
            login = _login_provider_for(data)
            result.append(
                {
                    "name": name,
                    "model": data.get("model", ""),
                    "provider": data.get("provider", ""),
                    "login_provider": login,
                    "available": _is_available(login),
                    "source": "preset",
                    "max_context": data.get("max_context", 0),
                    "max_output": data.get("max_output", 0),
                    "temperature": data.get("temperature"),
                    "reasoning_effort": data.get("reasoning_effort", ""),
                    "extra_body": data.get("extra_body", {}),
                    "base_url": data.get("base_url", ""),
                }
            )

    # Default
    default = get_default_model()
    for r in result:
        r["is_default"] = r["name"] == default or r["model"] == default

    return result
