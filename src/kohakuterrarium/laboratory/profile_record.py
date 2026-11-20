"""Serialize a selected preset and its effective transport for a laboratory worker."""

from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.resolve import _get_preset_definition
from kohakuterrarium.llm.profiles.types import LLMPreset


def profile_record(
    selector: str, *, definitions: dict[tuple[str, str], LLMPreset] | None = None
) -> dict | None:
    preset = _get_preset_definition(selector, definitions=definitions)
    if preset is None:
        return None
    backend = load_backends().get(preset.provider)
    if backend is None:
        return None
    return {
        **preset.to_dict(),
        "name": preset.name,
        "backend_type": backend.backend_type,
        "base_url": backend.base_url,
        "api_key_env": backend.api_key_env,
        "backend_provider_name": backend.provider_name,
        "backend_native_tools": list(backend.provider_native_tools),
    }
