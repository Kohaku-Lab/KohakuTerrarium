"""
LLM provider factory.

Creates the correct LLM provider based on:
  1. LLM profile (from config, CLI override, or default)
  2. Inline controller config (backward compat)
"""

from dataclasses import MISSING, fields
from typing import Any

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.llm.anthropic_provider import AnthropicProvider
from kohakuterrarium.llm.base import LLMConfig, LLMProvider
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm import api_keys as _api_keys
from kohakuterrarium.llm.profiles import LLMProfile, get_api_key, resolve_controller_llm
from kohakuterrarium.utils.env_interp import interpolate_env_vars
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _resolved_base_url(profile: LLMProfile) -> str | None:
    """Interpolate ``${VAR}`` in a profile's ``base_url`` at consume time.

    Provider config keeps ``${VAR}`` templates raw on disk (see
    ``llm.backends.load_backends``); they resolve here, when the provider
    is built, against the live environment. Returns ``None`` for an
    empty/unset url, and warns if a template resolved to empty (likely a
    missing env var) so an empty base_url isn't sent silently.
    """
    raw = (getattr(profile, "base_url", "") or "").strip()
    if not raw:
        return None
    resolved = interpolate_env_vars(raw).strip()
    if "${" in raw and not resolved:
        logger.warning(
            "base_url env interpolation resolved to empty",
            profile=getattr(profile, "name", "?"),
            raw=raw,
        )
    return resolved or None


_AGENT_CONFIG_FIELDS = {field.name: field for field in fields(AgentConfig)}


def _agent_config_default(field_name: str) -> Any:
    field = _AGENT_CONFIG_FIELDS[field_name]
    if field.default is not MISSING:
        return field.default
    if field.default_factory is not MISSING:
        return field.default_factory()
    return MISSING


def _is_meaningful_config_value(field_name: str, value: Any) -> bool:
    """Return True when a config value should override preset/default resolution."""
    if value is None:
        return False

    default = _agent_config_default(field_name)
    if isinstance(value, str):
        return value != "" and value != default
    if isinstance(value, dict):
        return bool(value)
    return value != default


def create_llm_provider(
    config: AgentConfig,
    llm: str | None = None,
) -> LLMProvider:
    """Create an LLM provider from agent config.

    Tries LLM profiles first (centralized config), falls back to
    inline controller settings (backward compat).

    Args:
        config: Agent configuration
        llm: Override selector — a profile / preset name or
            ``provider/model[@variations]`` string (the ``--llm`` CLI
            flag and every ``llm=`` API param land here).
    """
    # Try profile resolution
    controller_data = _extract_controller_data(config)
    profile = resolve_controller_llm(controller_data, llm)

    if profile:
        return _create_from_profile(profile)

    # Backward compat: inline config
    return _create_from_inline(config)


def coerce_llm_provider(
    llm: "LLMProvider | LLMProfile | str | None",
    config: AgentConfig,
) -> LLMProvider:
    """Turn any accepted ``llm=`` value into a provider instance.

    The single coercion point behind every ``llm=`` parameter
    (``Agent.build``, ``Terrarium.add_creature``, ``compose.agent``):

    - ``None`` → resolve from the agent config (profiles → inline).
    - ``str`` → selector: profile / preset name or
      ``provider/model[@variations]``.
    - :class:`LLMProfile` → instantiate that profile directly.
    - provider instance (anything with a ``chat`` coroutine, e.g.
      ``ScriptedLLM``) → used as-is, no resolution.

    Raises:
        TypeError: For any other type.
        LLMNotConfiguredError / ValueError: When resolution fails.
    """
    if llm is None:
        return create_llm_provider(config)
    if isinstance(llm, str):
        return create_llm_provider(config, llm)
    if isinstance(llm, LLMProfile):
        return _create_from_profile(llm)
    if callable(getattr(llm, "chat", None)):
        return llm
    raise TypeError(
        f"llm= accepts a provider instance, a selector string, an "
        f"LLMProfile, or None — got {type(llm).__name__}"
    )


def _extract_controller_data(config: AgentConfig) -> dict[str, Any]:
    """Extract only meaningful controller overrides for profile resolution."""
    data: dict[str, Any] = {}

    for field_name in (
        "model",
        "provider",
        "variation_selections",
        "variation",
        "auth_mode",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "service_tier",
        "extra_body",
        "retry_policy",
    ):
        value = getattr(config, field_name)
        if _is_meaningful_config_value(field_name, value):
            data[field_name] = dict(value) if isinstance(value, dict) else value

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

    if profile.backend_type == "fake_test":
        # Test-only backend — used by the multi-node test harness to
        # exercise the FULL profile resolution + api-key fetch chain
        # without performing any real network call.  The api_key value
        # is *required* (the credential lookup below raises if empty),
        # so a test using this backend genuinely proves the worker can
        # reach the host's identity store.  Imported lazily so the
        # production runtime never loads the ``testing`` package.
        api_key = get_api_key(profile.provider) if profile.provider else ""
        if not api_key and profile.api_key_env:
            api_key = get_api_key(profile.api_key_env)
        if not api_key:
            raise ValueError(
                f"API key not found for fake_test profile '{profile.name}' "
                f"(provider={profile.provider!r}) — this is the credential "
                "resolution path under test; set the key on the host."
            )
        from kohakuterrarium.testing.fake_llm_provider import FakeLLMProvider

        script_path = (profile.extra_body or {}).get("script_path") or None
        provider = FakeLLMProvider(
            api_key=api_key,
            model=profile.model or "fake-echo",
            script_path=str(script_path) if script_path else None,
        )
        provider._profile_max_context = profile.max_context
        _apply_backend_native_identity(provider, profile)
        return provider

    if profile.backend_type == "codex":
        # OpenAI Responses-API transport. ``base_url`` is the single
        # discriminator: set -> custom endpoint, API-key auth, NO OAuth
        # (key required); unset -> ChatGPT-subscription OAuth flow (no key
        # consulted, so a stray stored ``codex`` key can't hijack it).
        codex_base_url = _resolved_base_url(profile)
        codex_key: str | None = None
        if codex_base_url:
            resolved = get_api_key(profile.provider) if profile.provider else ""
            if not resolved and profile.api_key_env:
                resolved = get_api_key(profile.api_key_env)
            codex_key = interpolate_env_vars(resolved or "") or None
            if not codex_key:
                raise LLMNotConfiguredError(
                    f"API key required for the custom OpenAI Responses "
                    f"endpoint '{profile.name}' ({codex_base_url}). Set it "
                    f"via 'kt login {profile.provider or profile.name}' or "
                    f"the {profile.api_key_env or 'provider'} key."
                )
        provider = CodexOAuthProvider(
            model=profile.model,
            reasoning_effort=profile.reasoning_effort or "medium",
            service_tier=profile.service_tier or None,
            retry_policy=getattr(profile, "retry_policy", None),
            api_key=codex_key,
            base_url=codex_base_url,
        )
        provider._profile_max_context = profile.max_context
        _apply_backend_native_identity(provider, profile)
        return provider

    api_key = get_api_key(profile.provider) if profile.provider else ""
    if not api_key and profile.api_key_env:
        api_key = get_api_key(profile.api_key_env)
    # Resolve ``${VAR}`` if the stored key is itself a template.
    api_key = interpolate_env_vars(api_key or "")
    if not api_key:
        # Worker mode: ``llm.api_keys._resolver`` is set; the controller's
        # identity store is the only valid source.  Setting the env var
        # on the worker is explicitly NOT consulted (host-canonical
        # identity, per management-wiring.md § studio.identity).  Tell
        # the operator that instead of the generic ``kt login`` hint.
        if _api_keys._resolver is not None:
            raise ValueError(
                f"API key not found for profile '{profile.name}' (worker "
                f"mode).  The controller's identity store is the only "
                f"source — set the key on the host via "
                f"``POST /api/settings/keys`` or ``kt login "
                f"{profile.provider or 'openai'}`` (on the host).  The "
                f"worker's own env / file is intentionally NOT consulted."
            )
        raise ValueError(
            f"API key not found for profile '{profile.name}'. "
            f"Use 'kt login {profile.provider or 'openai'}' or set "
            f"{profile.api_key_env or 'OPENAI_API_KEY'} environment variable."
        )

    retry_policy = getattr(profile, "retry_policy", None)
    if profile.backend_type == "litellm":
        # LiteLLM is an optional extra (``kohakuterrarium[litellm]``).
        # Import the provider only when a litellm profile is actually
        # selected so core package imports / ``kt --help`` keep working
        # in a minimal install.
        from kohakuterrarium.llm.litellm_provider import LiteLLMProvider

        provider = LiteLLMProvider(
            model=profile.model,
            api_key=api_key or None,
            config=LLMConfig(
                model=profile.model,
                temperature=profile.temperature,
                max_tokens=profile.max_output or None,
            ),
        )
        provider._profile_max_context = profile.max_context
        _apply_backend_native_identity(provider, profile)
        return provider

    base_url = _resolved_base_url(profile)
    if profile.backend_type == "anthropic":
        provider = AnthropicProvider(
            api_key=api_key,
            base_url=base_url,
            model=profile.model,
            temperature=profile.temperature,
            max_tokens=profile.max_output or None,
            extra_body=profile.extra_body or None,
            service_tier=profile.service_tier or None,
            retry_policy=retry_policy,
        )
    else:
        provider = OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
            model=profile.model,
            temperature=profile.temperature,
            max_tokens=profile.max_output or None,
            extra_body=profile.extra_body or None,
            retry_policy=retry_policy,
        )
    provider._profile_max_context = profile.max_context
    # The backend NAME (``"openrouter"``, ``"openai"``, ``"anthropic"``,
    # ...) is the api_keys.yaml lookup key used at boot. Stash it on the
    # provider so ``reload_credentials`` can re-fetch the same way when
    # the user updates a key via Settings → Providers — built-in
    # backends leave the native-tool ``provider_name`` empty, so the
    # native-tool field alone is not enough.
    if profile.provider:
        provider._credential_provider = profile.provider
    _apply_backend_native_identity(provider, profile)
    return provider


def _apply_backend_native_identity(provider: LLMProvider, profile: LLMProfile) -> None:
    """Stamp the backend's provider_name and provider_native_tools onto the
    instance.

    The tool-injection logic in :mod:`bootstrap.agent_init` reads these
    via ``getattr(llm, "provider_name")`` / ``provider_native_tools``.
    Class-level defaults on the provider subclass serve as fallbacks:
    a custom provider that leaves ``provider_name`` empty and declares
    no native tools inherits the class defaults (empty sets).
    """
    backend_name = getattr(profile, "backend_provider_name", "")
    if backend_name:
        provider.provider_name = backend_name
    backend_tools = getattr(profile, "backend_native_tools", None)
    if backend_tools is not None:
        # Always respect the backend's list (including the empty list —
        # an explicit empty list means "opt out of every native tool").
        provider.provider_native_tools = frozenset(backend_tools)


def create_llm_from_profile_name(name: str) -> LLMProvider:
    """Create an LLM provider from a profile/preset name.

    Used for live model switching. Resolves the name to a profile,
    then creates the appropriate provider.

    Raises:
        ValueError: If profile not found or API key missing.
    """
    profile = resolve_controller_llm({}, llm=name)
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
            retry_policy=config.retry_policy,
        )
        logger.info(
            "Using Codex OAuth provider (ChatGPT subscription)",
            model=config.model,
        )
        return provider

    # Standard API key auth (OpenAI, OpenRouter, etc.). Native Anthropic is
    # explicit here so legacy inline ``provider: anthropic`` OpenAI-compatible
    # configs keep using the OpenAI-compatible transport.
    api_key = config.get_api_key()
    if not api_key:
        env_hint = (
            f"Set the {config.api_key_env} environment variable."
            if config.api_key_env
            else "Configure an api_key_env in the agent config or use "
            "'kt login <provider>'."
        )
        raise LLMNotConfiguredError(
            f"API key not found for inline model {config.model!r}. {env_hint}"
        )

    if config.auth_mode == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            base_url=config.base_url or None,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            extra_body=config.extra_body or None,
            service_tier=config.service_tier,
            retry_policy=config.retry_policy,
        )

    return OpenAIProvider(
        api_key=api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        extra_body=config.extra_body or None,
        retry_policy=config.retry_policy,
    )
