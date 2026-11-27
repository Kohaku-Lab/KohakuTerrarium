"""YAML I/O for user-defined presets + migration from the legacy layout.

Read and write provider-scoped presets while accepting legacy flat layouts.
"""

from typing import Any

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.catalog_endpoints import CATALOG_ENDPOINTS
from kohakuterrarium.llm.profiles.backends import (
    _BUILTIN_PROVIDER_NAMES,
    catalog_backend,
    editing_profiles,
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


def _unreadable_entry(location: str) -> None:
    # Read-only callers can still use the other presets. Editing a partial
    # inventory would permanently discard the entry on the next rewrite.
    if editing_profiles():
        raise ConfigError(
            f"Cannot read {location} in llm_profiles.yaml; settings were not saved."
        )


def _presets_from_section(
    stored: Any, section: str
) -> dict[tuple[str, str], LLMPreset]:
    """Read flat entries and nested provider buckets, including mixed layouts."""
    presets: dict[tuple[str, str], LLMPreset] = {}
    if not isinstance(stored, dict):
        _unreadable_entry(section)
        return presets
    for key, entry in stored.items():
        if not isinstance(key, str) or not key or not isinstance(entry, dict):
            _unreadable_entry(f"{section}.{key}")
            continue
        # A string model is a legacy preset. A mapping named "model" can
        # itself be a user preset inside a current-format provider bucket.
        if isinstance(entry.get("model"), str):
            # An inline transport may share a key with a provider bucket.
            # A sibling preset cannot be folded into the flat record.
            if editing_profiles():
                for name, value in entry.items():
                    if name in {
                        "extra_body",
                        "variation_groups",
                        "retry_policy",
                        "context_aliases",
                    }:
                        continue
                    if isinstance(value, dict) and isinstance(value.get("model"), str):
                        _unreadable_entry(f"{section}.{key}.{name}")
            inferred = _legacy_provider_from_data(entry)
            preset = preset_from_data(
                key, entry, inferred if inferred in CATALOG_ENDPOINTS else ""
            )
            if preset.provider:
                presets.setdefault((preset.provider, key), preset)
            else:
                _unreadable_entry(f"{section}.{key}")
            continue
        for name, body in entry.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(body, dict)
                or not isinstance(body.get("model"), str)
            ):
                _unreadable_entry(f"{section}.{key}.{name}")
                continue
            presets[(key, name)] = preset_from_data(name, body, key)
    return presets


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


def load_presets(*, include_remote: bool = True) -> dict[tuple[str, str], LLMPreset]:
    """Load nested or legacy presets, with worker-local entries overriding host fallbacks."""
    data = _load_yaml()
    presets = _presets_from_section(data.get("profiles", {}), "profiles")
    presets.update(_presets_from_section(data.get("presets", {}), "presets"))
    if include_remote and _remote_presets:
        return {**_remote_presets, **presets}
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
        if (
            name not in _BUILTIN_PROVIDER_NAMES
            or (name in CATALOG_ENDPOINTS and backend != catalog_backend(name))
        )
        and backend != package_records.get(name)
    }
    if user_backends:
        data["backends"] = user_backends
    if presets:
        nested: dict[str, dict[str, Any]] = {}
        for (provider, name), preset in presets.items():
            body = preset.to_stored_dict()
            body.pop("provider", None)
            nested.setdefault(provider, {})[name] = body
        data["presets"] = nested
    return data
