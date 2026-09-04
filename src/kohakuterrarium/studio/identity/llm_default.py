"""Default-model selection — read/write the canonical default identifier."""

from typing import Any

from kohakuterrarium.llm.profiles import (
    get_default_model,
    list_all,
    profile_to_identifier,
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
    """Set the default model identifier, or clear it with an empty string."""
    set_default_model(identifier)
    return identifier


def resolve_and_set_default(name: str) -> tuple[str, str | None]:
    """Resolve a bare or qualified model name and make it the default.

    Success returns the canonical identifier with no error. Failure returns an
    empty identifier and a caller-facing message.
    """
    _ = split_identifier(name)
    try:
        profile = get_profile_for_identifier(name)
    except ValueError as e:
        return "", str(e)
    if not profile:
        return "", f"Preset not found: {name}"
    # Round-trip the variation selections too; rebuilding "provider/name" by
    # hand dropped them, so `kt model default x@reasoning=ultra` silently
    # stored plain x.
    identifier = profile_to_identifier(profile)
    set_default_model(identifier)
    return identifier, None


def list_all_models_combined() -> list[dict[str, Any]]:
    """Return the HTTP-facing combined view of user and built-in models."""
    return list_all()
