"""YAML I/O for user-defined presets + migration from the legacy layout.

Split out of :mod:`profiles` to keep that module under the
per-file line-count guard. The write path (``_serialize_user_data``)
emits the current nested ``{provider: {name: data}}`` shape; the
read path (``load_presets``) accepts both nested and legacy flat
``{name: {provider, model, ...}}`` layouts and migrates to the
nested shape on the next save.
"""

from typing import Any

from kohakuterrarium.llm.backends import _BUILTIN_PROVIDER_NAMES, _SCHEMA_VERSION
from kohakuterrarium.llm.backends import (
    legacy_provider_from_data as _legacy_provider_from_data,
)
from kohakuterrarium.llm.backends import load_yaml_store as _load_yaml
from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset


def preset_from_data(name: str, data: dict[str, Any], provider: str = "") -> LLMPreset:
    """Build a LLMPreset from raw yaml data.

    ``provider`` takes priority over any ``provider`` key already on
    ``data`` — callers passing this in have unambiguous knowledge of
    which provider bucket the entry came from (new nested YAML shape).
    For legacy flat YAML, falls back to inferring from ``data``.
    """
    preset = LLMPreset.from_dict(name, data)
    if provider:
        preset.provider = provider
    if not preset.provider:
        preset.provider = _legacy_provider_from_data(data)
    return preset


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
    """Read a legacy flat ``{name: data}`` presets block.

    The pre-2026-05 YAML shape used a single-level dict keyed by preset
    name, with the provider inlined as a ``provider`` field on each
    entry. This reader reconstructs ``(provider, name)`` keys from
    those fields. Entries without a resolvable provider are dropped.
    """
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
    """Heuristic: does a ``presets`` block use the nested layout?

    Nested: the top-level values are dicts of preset entries (no
    ``model`` key directly). Flat: the values are preset entries with
    a ``model`` key.
    """
    for value in stored.values():
        if not isinstance(value, dict):
            return False
        if "model" in value:
            return False
        for child in value.values():
            if isinstance(child, dict):
                return True
    return True


def load_presets() -> dict[tuple[str, str], LLMPreset]:
    """Return user-defined presets keyed by ``(provider, name)``.

    Accepts both the current nested YAML shape
    (``presets: {provider: {name: data}}``) and the legacy flat shape
    (``presets: {name: {provider: ..., ...}}``). The next
    :func:`kohakuterrarium.llm.profiles.save_profile` rewrites the
    file in the nested shape.
    """
    data = _load_yaml()
    stored = data.get("presets", {})
    if isinstance(stored, dict) and _looks_nested(stored):
        return _load_nested_presets(stored)

    presets = _load_flat_presets_legacy(stored)
    legacy = data.get("profiles", {})
    if isinstance(legacy, dict) and not _looks_nested(legacy):
        for key, preset in _load_flat_presets_legacy(legacy).items():
            presets.setdefault(key, preset)
    return presets


def serialize_user_data(
    presets: dict[tuple[str, str], LLMPreset],
    backends: dict[str, LLMBackend],
    default_model: str = "",
) -> dict[str, Any]:
    """Produce the YAML payload for ``~/.kohakuterrarium/llm_profiles.yaml``."""
    data: dict[str, Any] = {"version": _SCHEMA_VERSION}
    if default_model:
        data["default_model"] = default_model
    user_backends = {
        name: backend.to_dict()
        for name, backend in backends.items()
        if name not in _BUILTIN_PROVIDER_NAMES
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
