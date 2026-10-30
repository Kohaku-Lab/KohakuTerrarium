"""YAML I/O for user-defined presets + migration from the legacy layout.

Read and write provider-scoped presets while accepting legacy flat layouts.
"""

from typing import Any

from kohakuterrarium.llm.profiles.backends import (
    _BUILTIN_PROVIDER_NAMES,
    _SCHEMA_VERSION,
)
from kohakuterrarium.llm.profiles.backends import (
    legacy_provider_from_data as _legacy_provider_from_data,
)
from kohakuterrarium.llm.profiles.backends import load_yaml_store as _load_yaml
from kohakuterrarium.llm.profiles.backends import package_provider_records
from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset


def preset_from_data(name: str, data: dict[str, Any], provider: str = "") -> LLMPreset:
    """Build a preset, preferring an explicit provider bucket over legacy fields."""
    preset = LLMPreset.from_dict(name, data)
    if provider:
        preset.provider = provider
    if not preset.provider:
        preset.provider = _legacy_provider_from_data(data)
    return preset


# Workers cache host-published presets so synchronous provider construction needs no I/O.
_remote_presets: dict[tuple[str, str], LLMPreset] = {}


def set_remote_preset(provider: str, name: str, preset: LLMPreset) -> None:
    """Cache a host-resolved preset for synchronous worker lookup."""
    _remote_presets[(provider, name)] = preset


def clear_remote_presets() -> None:
    """Invalidate all host-resolved preset entries."""
    _remote_presets.clear()


def _load_nested_presets(stored: Any) -> dict[tuple[str, str], LLMPreset]:
    """Read a presets block in nested ``{provider: {name: data}}`` shape."""
    presets: dict[tuple[str, str], LLMPreset] = {}
    if not isinstance(stored, dict):
        return presets
    for provider, bucket in stored.items():
        if not isinstance(bucket, dict):
            continue
        for name, pdata in bucket.items():
            if isinstance(pdata, dict):
                presets[(provider, name)] = preset_from_data(name, pdata, provider)
    return presets


def _load_flat_presets_legacy(stored: Any) -> dict[tuple[str, str], LLMPreset]:
    """Read flat legacy presets and reconstruct provider/name keys."""
    presets: dict[tuple[str, str], LLMPreset] = {}
    if not isinstance(stored, dict):
        return presets
    for name, pdata in stored.items():
        if not isinstance(pdata, dict):
            continue
        preset = preset_from_data(name, pdata)
        if not preset.provider:
            continue
        presets[(preset.provider, preset.name)] = preset
    return presets


def _looks_nested(stored: dict[str, Any]) -> bool:
    """Detect nested provider buckets, treating empty data as legacy-compatible."""
    if not stored:
        return False
    for value in stored.values():
        if not isinstance(value, dict):
            return False
        if "model" in value:
            return False
        for child in value.values():
            if isinstance(child, dict):
                return True
    return True


def _string_mapping(stored: Any) -> dict[str, str]:
    """Keep non-empty string keys whose selector values are non-blank."""
    if not isinstance(stored, dict):
        return {}
    return {
        name: selector
        for name, selector in stored.items()
        if isinstance(name, str)
        and name
        and isinstance(selector, str)
        and selector.strip()
    }


def get_subagent_models() -> dict[str, str]:
    """Return exact sub-agent name to model-selector defaults."""
    return _string_mapping(_load_yaml().get("subagent_models", {}))


def get_child_models() -> dict[str, str]:
    """Return package-ref and global child model defaults."""
    return _string_mapping(_load_yaml().get("child_models", {}))


def load_presets() -> dict[tuple[str, str], LLMPreset]:
    """Load nested or legacy presets, with host-resolved worker entries authoritative."""
    data = _load_yaml()
    stored = data.get("presets", {})
    if isinstance(stored, dict) and _looks_nested(stored):
        presets = _load_nested_presets(stored)
    else:
        presets = _load_flat_presets_legacy(stored)
        legacy = data.get("profiles", {})
        if isinstance(legacy, dict) and not _looks_nested(legacy):
            for key, preset in _load_flat_presets_legacy(legacy).items():
                presets.setdefault(key, preset)
    if _remote_presets:
        presets.update(_remote_presets)
    return presets


def serialize_user_data(
    presets: dict[tuple[str, str], LLMPreset],
    backends: dict[str, LLMBackend],
    default_model: str = "",
    subagent_models: dict[str, str] | None = None,
    child_models: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialize user providers and presets in the current nested schema."""
    data: dict[str, Any] = {"version": _SCHEMA_VERSION}
    if default_model:
        data["default_model"] = default_model
    if subagent_models:
        data["subagent_models"] = _string_mapping(subagent_models)
    if child_models:
        data["child_models"] = _string_mapping(child_models)
    package_records = package_provider_records()
    user_backends = {
        name: backend.to_dict()
        for name, backend in backends.items()
        if name not in _BUILTIN_PROVIDER_NAMES and backend != package_records.get(name)
    }
    if user_backends:
        data["backends"] = user_backends
    if presets:
        nested: dict[str, dict[str, Any]] = {}
        for (provider, name), preset in presets.items():
            body = preset.to_dict()
            body.pop("provider", None)
            nested.setdefault(provider, {})[name] = body
        data["presets"] = nested
    return data
