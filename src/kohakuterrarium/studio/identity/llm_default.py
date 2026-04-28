"""Default-model selection — read/write the canonical default identifier."""

from typing import Any

from kohakuterrarium.llm.profiles import (
    get_default_model,
    list_all,
    set_default_model,
)
from kohakuterrarium.studio.identity.llm_profiles import (
    get_profile_for_identifier,
    split_identifier,
)


def get_default() -> str:
    """Return the configured default model as ``provider/name``."""
    return get_default_model()


def set_default(identifier: str) -> str:
    """Set the default model. Empty string clears it."""
    set_default_model(identifier)
    return identifier


def resolve_and_set_default(name: str) -> tuple[str, str | None]:
    """Resolve ``name`` (bare or qualified) and set it as the default.

    Returns ``(canonical_identifier, None)`` on success. On failure returns
    ``("", error_message)`` where the message is suitable for direct
    surfacing to the CLI/HTTP caller.
    """
    _ = split_identifier(name)
    try:
        profile = get_profile_for_identifier(name)
    except ValueError as e:
        return "", str(e)
    if not profile:
        return "", f"Preset not found: {name}"
    identifier = f"{profile.provider}/{profile.name}"
    set_default_model(identifier)
    return identifier, None


def list_all_models_combined() -> list[dict[str, Any]]:
    """Return the combined view of user + built-in models (HTTP shape)."""
    return list_all()
