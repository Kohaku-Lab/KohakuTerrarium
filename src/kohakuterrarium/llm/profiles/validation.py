"""Field validation for presets a user, an editor or a package supplies."""

from kohakuterrarium.llm.profiles.context import context_aliases
from kohakuterrarium.llm.profiles.types import LLMPreset
from kohakuterrarium.llm.profiles.variations import check_variation_groups


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def check_preset(preset: LLMPreset) -> None:
    """Raise ``ValueError`` naming the first field that cannot be stored."""
    for name in ("max_context", "max_output"):
        if not _positive_int(getattr(preset, name)):
            raise ValueError(f"{name} must be a positive whole number")
    if preset.context_window is not None and not _positive_int(preset.context_window):
        raise ValueError("context_window must be a positive whole number or empty")
    temperature = preset.temperature
    if temperature is not None and (
        isinstance(temperature, bool) or not isinstance(temperature, (int, float))
    ):
        raise ValueError("temperature must be a number or empty")
    for name in ("reasoning_effort", "service_tier"):
        if not isinstance(getattr(preset, name), str):
            raise ValueError(f"{name} must be text")
    if not isinstance(preset.extra_body, dict):
        raise ValueError("extra_body must be an object")
    if preset.retry_policy is not None and not isinstance(preset.retry_policy, dict):
        raise ValueError("retry_policy must be an object or empty")
    tools = preset.provider_native_tools
    if tools is not None and (
        not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)
    ):
        raise ValueError("provider_native_tools must be a list of tool names")
    aliases = preset.context_aliases
    if not isinstance(aliases, dict) or context_aliases(aliases) != aliases:
        raise ValueError("context_aliases must map names to positive whole numbers")
    check_variation_groups(preset.variation_groups)
