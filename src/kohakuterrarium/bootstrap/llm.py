"""
LLM provider factory.

Creates the correct LLM provider based on:
  1. LLM profile (from config, CLI override, or default)
  2. Inline controller config (backward compat)
"""

from typing import Any

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.profiles import LLMProfile, get_api_key, resolve_controller_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def create_llm_provider(
    config: AgentConfig,
    llm_override: str | None = None,
) -> LLMProvider:
    """Create an LLM provider from agent config.

    Tries LLM profiles first (centralized config), falls back to
    inline controller settings (backward compat).

    Args:
        config: Agent configuration
        llm_override: Override profile name (from --llm CLI flag)
    """
    # Try profile resolution
    controller_data = _extract_controller_data(config)
    profile = resolve_controller_llm(controller_data, llm_override)

    if profile:
        return _create_from_profile(profile)

    # Backward compat: inline config
    return _create_from_inline(config)


def _extract_controller_data(config: AgentConfig) -> dict[str, Any]:
    """Extract controller dict for profile resolution."""
    data: dict[str, Any] = {}
    if config.model:
        data["model"] = config.model
    if config.auth_mode:
        data["auth_mode"] = config.auth_mode
    if config.temperature is not None:
        data["temperature"] = config.temperature
    if config.max_tokens:
        data["max_tokens"] = config.max_tokens
    if config.reasoning_effort:
        data["reasoning_effort"] = config.reasoning_effort
    if config.service_tier:
        data["service_tier"] = config.service_tier
    # Check for llm profile reference
    llm_ref = getattr(config, "llm_profile", None)
    if llm_ref:
        data["llm"] = llm_ref
    return data


def _create_from_profile(profile: LLMProfile) -> LLMProvider:
    """Create LLM provider from a resolved profile."""
    logger.info(
        "Using LLM profile",
        profile=profile.name,
        model=profile.model,
        provider=profile.provider,
        backend_type=profile.backend_type,
    )

    if profile.backend_type == "codex":
        provider = CodexOAuthProvider(
            model=profile.model,
            reasoning_effort=profile.reasoning_effort or "medium",
            service_tier=profile.service_tier or None,
        )
        provider._profile_max_context = profile.max_context
        return provider

    api_key = get_api_key(profile.provider) if profile.provider else ""
    if not api_key and profile.api_key_env:
        api_key = get_api_key(profile.api_key_env)
    if not api_key:
        raise ValueError(
            f"API key not found for profile '{profile.name}'. "
            f"Use 'kt login {profile.provider or 'openai'}' or set "
            f"{profile.api_key_env or 'OPENAI_API_KEY'} environment variable."
        )

    provider = OpenAIProvider(
        api_key=api_key,
        base_url=profile.base_url or None,
        model=profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_output or None,
        extra_body=profile.extra_body or None,
    )
    provider._profile_max_context = profile.max_context
    return provider


def create_llm_from_profile_name(name: str) -> LLMProvider:
    """Create an LLM provider from a profile/preset name.

    Used for live model switching. Resolves the name to a profile,
    then creates the appropriate provider.

    Raises:
        ValueError: If profile not found or API key missing.
    """
    profile = resolve_controller_llm({}, llm_override=name)
    if not profile:
        raise ValueError(f"Model profile not found: {name}")
    return _create_from_profile(profile)


def _create_from_inline(config: AgentConfig) -> LLMProvider:
    """Create LLM provider from inline controller config (backward compat)."""
    if not config.model:
        raise ValueError(
            "No LLM model configured and no default model set. "
            "Use 'kt login <provider>' to authenticate, then "
            "'kt model default <name>' to set a default, "
            "or add 'llm: <profile>' to your creature config."
        )

    if config.auth_mode == "codex-oauth":
        provider = CodexOAuthProvider(
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            service_tier=config.service_tier,
        )
        logger.info(
            "Using Codex OAuth provider (ChatGPT subscription)",
            model=config.model,
        )
        return provider

    # Standard API key auth (OpenAI, OpenRouter, etc.)
    api_key = config.get_api_key()
    if not api_key:
        raise ValueError(
            f"API key not found. Set {config.api_key_env} environment variable."
        )

    return OpenAIProvider(
        api_key=api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        extra_body=config.extra_body or None,
    )
