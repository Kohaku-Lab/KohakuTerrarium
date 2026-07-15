"""Sandbox profile parsing helpers."""

from typing import Any

from kohakuterrarium.modules.sandbox.presets import get_profile
from kohakuterrarium.modules.sandbox.profile import SandboxProfile


def parse_profile(
    value: str | dict[str, Any] | SandboxProfile | None,
) -> SandboxProfile:
    """Normalize a preset name, mapping, profile instance, or default profile."""
    if isinstance(value, SandboxProfile):
        return value
    if isinstance(value, dict):
        return SandboxProfile.from_dict(value)
    return get_profile(value or "WORKSPACE")
