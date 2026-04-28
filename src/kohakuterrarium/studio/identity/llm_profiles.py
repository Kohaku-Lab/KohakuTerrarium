"""LLM preset (profile) CRUD — list/save/delete user presets.

Wraps :mod:`kohakuterrarium.llm.profiles`. The legacy bare-name profile
delete (without provider) is preserved here as a documented one-release
shim — see ``remove_profile_legacy`` and the matching route in
``api/routes/identity/llm.py``.
"""

from typing import Any

from kohakuterrarium.llm.profiles import (
    LLMPreset,
    _get_preset_definition,
    delete_profile,
    get_default_model,
    get_profile,
    list_all,
    load_backends,
    load_presets,
    load_profiles,
    save_profile,
    set_default_model,
)


def list_profiles_payload() -> list[dict[str, Any]]:
    """Return every user-defined LLM profile as a plain-dict payload."""
    profiles = load_profiles()
    presets = load_presets()
    return [
        {
            "name": p.name,
            "model": p.model,
            "provider": p.provider,
            "backend_type": p.backend_type,
            "base_url": p.base_url or "",
            "api_key_env": p.api_key_env or "",
            "max_context": p.max_context,
            "max_output": p.max_output,
            "temperature": p.temperature,
            "reasoning_effort": p.reasoning_effort or "",
            "service_tier": p.service_tier or "",
            "extra_body": p.extra_body or {},
            "variation_groups": (
                presets[key].variation_groups if key in presets else {}
            ),
            "selected_variations": p.selected_variations or {},
        }
        for key, p in profiles.items()
    ]


def list_user_profile_keys() -> list[tuple[str, str]]:
    """Return the ``(provider, name)`` keys of all user-defined profiles."""
    return list(load_profiles().keys())


def split_identifier(name: str) -> tuple[str, str]:
    """Split ``provider/name`` into ``(provider, name)``; bare → ``("", name)``."""
    if "/" in name:
        prov, bare = name.split("/", 1)
        return prov, bare
    return "", name


def get_profile_for_identifier(identifier: str) -> Any:
    """Resolve ``provider/name`` or bare ``name`` to a :class:`LLMProfile`.

    Raises ``ValueError`` when the bare-name lookup is ambiguous.
    """
    provider, bare = split_identifier(identifier)
    return get_profile(bare, provider)


def save_profile_record(
    name: str,
    model: str,
    provider: str,
    max_context: int = 128000,
    max_output: int = 16384,
    temperature: float | None = None,
    reasoning_effort: str = "",
    service_tier: str = "",
    extra_body: dict[str, Any] | None = None,
    variation_groups: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> LLMPreset:
    """Validate + persist a preset. Raises ``ValueError`` on bad input."""
    if not name or not model or not provider:
        raise ValueError("Name, model, and provider are required")
    if provider not in load_backends():
        raise ValueError(f"Provider not found: {provider}")
    preset = LLMPreset(
        name=name,
        model=model,
        provider=provider,
        max_context=max_context,
        max_output=max_output,
        temperature=temperature,
        reasoning_effort=reasoning_effort or "",
        service_tier=service_tier or "",
        extra_body=extra_body or {},
        variation_groups=variation_groups or {},
    )
    save_profile(preset)
    return preset


def remove_profile(name: str, provider: str = "") -> bool:
    """Delete a preset under ``(provider, name)``. Returns False if missing."""
    return delete_profile(name, provider)


# TODO Phase 3: remove legacy bare-name profile-delete (P5)
def remove_profile_legacy(name: str) -> bool:
    """Legacy bare-name delete — succeeds only when the bare name is unambiguous.

    Kept for older API clients. Prefer :func:`remove_profile` with an
    explicit ``provider`` since the canonical key is ``(provider, name)``.
    Documented for removal in the next release.
    """
    return delete_profile(name)


def get_preset_definition(name: str, provider: str = "") -> Any:
    return _get_preset_definition(name, provider)


def get_default_model_identifier() -> str:
    return get_default_model()


def set_default_model_identifier(identifier: str) -> None:
    set_default_model(identifier)


def list_all_models() -> list[dict[str, Any]]:
    """Return user + built-in presets resolved against current providers."""
    return list_all()
