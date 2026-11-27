"""Preset drafts for the models a provider's catalog reports.

A draft is the field set the Settings and CLI catalog pickers save as a new
preset. A matching user name takes precedence, then a unique model alias, else it
mirrors an existing built-in or inherits its nearest sibling, or turns the reported
reasoning levels into the backend's own reasoning group. A package backend's
suggested fields win over all of that.
"""

import re
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from kohakuterrarium.llm.catalog_endpoints import catalog_kind
from kohakuterrarium.llm.presets.catalog import get_all_presets
from kohakuterrarium.llm.presets.groups import (
    _ANTHROPIC_EFFORT_47_GROUP,
    _GEMINI_THINKING_GROUP_WITH_MINIMAL,
    _GPT6_REASONING_GROUP,
    _GROK_EFFORT_GROUP,
    _OPENAI_56_REASONING_GROUP,
    _OR_REASONING_GROUP_TOP,
)
from kohakuterrarium.llm.presets.store import load_presets
from kohakuterrarium.llm.profiles.backends import load_backends
from kohakuterrarium.llm.profiles.context import fold_preset_fields
from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset
from kohakuterrarium.llm.profiles.validation import check_preset
from kohakuterrarium.llm.profiles.variations import (
    CONTEXT_SELECTION_KEY,
    apply_patch_map,
)
from kohakuterrarium.utils.env_interp import interpolate_env_vars
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Preset fields a draft may carry besides provider, name and model.
DRAFT_FIELDS = (
    "max_context",
    "max_output",
    "context_window",
    "temperature",
    "reasoning_effort",
    "service_tier",
    "extra_body",
    "provider_native_tools",
    "variation_groups",
    "context_aliases",
)

# Fields copied from a same-family sibling preset.
_INHERITED = (
    "reasoning_effort",
    "service_tier",
    "extra_body",
    "provider_native_tools",
)

# Groups whose options are reasoning levels.
_REASONING_GROUPS = ("reasoning", "thinking")

_DATED = re.compile(r"(.+)-(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8})")
_VERSION = re.compile(r"\d+(?:[.-]\d+)*")
_SEPARATORS = "-_/:@"

_ENDPOINT_KINDS = {
    "https://api.openai.com/v1": "openai",
    "https://openrouter.ai/api/v1": "openrouter",
    "https://generativelanguage.googleapis.com/v1beta/openai": "gemini",
}
_ANTHROPIC_ENDPOINT = "https://api.anthropic.com"

# Per backend kind: the group name and the built-in group whose request field
# a reported level is written to.
_KIND_TEMPLATES: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {
    "codex": ("reasoning", _GPT6_REASONING_GROUP),
    "openai": ("reasoning", _OPENAI_56_REASONING_GROUP),
    "openrouter": ("reasoning", _OR_REASONING_GROUP_TOP),
    "gemini": ("thinking", _GEMINI_THINKING_GROUP_WITH_MINIMAL),
    "anthropic": ("reasoning", _ANTHROPIC_EFFORT_47_GROUP),
    "grok": ("reasoning", _GROK_EFFORT_GROUP),
    "deepseek": ("reasoning", {"high": {"extra_body.reasoning_effort": "high"}}),
    "opencode-go": ("reasoning", {"high": {"extra_body.reasoning_effort": "high"}}),
}


def draft_kind(backend: LLMBackend | None) -> str:
    """The request shape a backend's reasoning levels map onto, or ``""``."""
    if backend is None:
        return ""
    base_url = (interpolate_env_vars(backend.base_url) or "").rstrip("/")
    match backend.backend_type:
        case "codex":
            return "codex"
        case "grok-subscription":
            return "grok"
        case "anthropic":
            return "anthropic" if base_url in ("", _ANTHROPIC_ENDPOINT) else ""
        case "openai":
            return _ENDPOINT_KINDS.get(base_url) or catalog_kind(base_url)
    return ""


def model_family(model_id: str) -> tuple[str, tuple[int, ...]] | None:
    """Split a model id into its family (last version as ``*``) and that version."""
    for match in reversed(list(_VERSION.finditer(model_id))):
        start, end = match.span()
        if end < len(model_id) and model_id[end] not in _SEPARATORS:
            continue
        version = tuple(int(part) for part in re.split(r"[.-]", match[0]))
        return model_id[:start] + "*" + model_id[end:], version
    return None


def nearest_sibling(
    provider: str, model_id: str, catalog: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any] | None:
    """The same-family preset with the highest version below ``model_id``, else above."""
    target = model_family(model_id)
    if target is None:
        return None
    below: list[tuple[tuple[int, ...], dict[str, Any]]] = []
    above: list[tuple[tuple[int, ...], dict[str, Any]]] = []
    for (owner, _), data in catalog.items():
        other = data.get("model", "")
        found = model_family(other) if owner == provider and other != model_id else None
        if found is None or found[0] != target[0]:
            continue
        (below if found[1] <= target[1] else above).append((found[1], data))
    if below:
        return max(below, key=lambda item: item[0])[1]
    if above:
        return min(above, key=lambda item: item[0])[1]
    return None


def _dotted(data: dict[str, Any], path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _default_option(base: dict[str, Any], options: dict[str, dict]) -> str:
    for name, patch in options.items():
        if patch and all(_dotted(base, p) == v for p, v in patch.items()):
            return name
    return ""


def _trim_reasoning(fields: dict[str, Any], levels: tuple[str, ...]) -> dict[str, Any]:
    """Keep reported levels in each reasoning group and keep its default valid."""
    groups = fields.get("variation_groups", {})
    for group_name in _REASONING_GROUPS:
        options = groups.get(group_name)
        if not options or not levels:
            continue
        kept = {name: patch for name, patch in options.items() if name in levels}
        if not kept:
            continue
        default = _default_option(fields, options)
        groups[group_name] = kept
        if not default or default in kept:
            continue
        order = list(options)
        lower = [name for name in kept if order.index(name) < order.index(default)]
        choice = lower[-1] if lower else next(lvl for lvl in levels if lvl in kept)
        fields.update(apply_patch_map(fields, kept[choice]))
    return fields


def _inherit(sibling: dict[str, Any], levels: tuple[str, ...]) -> dict[str, Any]:
    fields = {key: deepcopy(sibling[key]) for key in _INHERITED if key in sibling}
    groups = {
        name: deepcopy(options)
        for name, options in (sibling.get("variation_groups") or {}).items()
        if name != CONTEXT_SELECTION_KEY
    }
    if groups:
        fields["variation_groups"] = groups
    return _trim_reasoning(fields, levels)


def _level_patch(template: dict[str, dict[str, Any]], level: str) -> dict[str, Any]:
    if level in template:
        return deepcopy(template[level])
    ((path, sample),) = template[next(reversed(template))].items()
    return {path: level.upper() if sample.isupper() else level}


def _kind_reasoning(kind: str, levels: tuple[str, ...]) -> dict[str, Any]:
    """A reasoning group in the backend's request shape, or nothing."""
    if kind not in _KIND_TEMPLATES or not levels:
        return {}
    group_name, template = _KIND_TEMPLATES[kind]
    fields: dict[str, Any] = {
        "variation_groups": {
            group_name: {level: _level_patch(template, level) for level in levels}
        }
    }
    if kind == "codex":
        # The Codex transport sends medium when a preset names no effort.
        fields["reasoning_effort"] = "medium" if "medium" in levels else levels[0]
    return fields


def _package_fields(provider: str, model: Any, base: dict[str, Any]) -> dict[str, Any]:
    """Validated package-suggested fields, or none when the suggestion is unusable."""
    suggested = model.preset
    if suggested is None:
        return {}
    try:
        if not isinstance(suggested, dict):
            raise ValueError("the suggested preset must be an object")
        fields = {key: suggested[key] for key in DRAFT_FIELDS if key in suggested}
        merged = {key: base[key] for key in DRAFT_FIELDS if key in base} | fields
        check_preset(
            LLMPreset(name=base["name"], model=model.id, provider=provider, **merged)
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Package preset suggestion dropped",
            provider=provider,
            model_id=model.id,
            error=str(exc),
        )
        return {}
    return deepcopy(fields)


def _mirror(provider: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
    preset = LLMPreset.from_dict(name, data).to_dict()
    fields = {key: preset[key] for key in DRAFT_FIELDS if key in preset}
    return {"provider": provider, "name": name, "model": data.get("model", "")} | fields


def _draft_name(model_id: str) -> str:
    return re.sub(r"[/@]", "-", model_id)


def matching_presets(
    provider: str, model_id: str, catalog: dict, saved: dict
) -> list[str]:
    """Find saved names for one provider row, preserving user alias ambiguity."""
    name = _draft_name(model_id)
    if (provider, name) in saved:
        return [name]
    aliases = sorted(
        name
        for (owner, name), p in saved.items()
        if owner == provider and p.model == model_id
    )
    if aliases:
        return aliases
    for key, data in catalog.items():
        if key[0] == provider and key not in saved and data.get("model") == model_id:
            return [key[1]]
    return []


def draft_with_matches(
    provider: str, model: Any, kind: str, catalog: dict, saved: dict
) -> tuple[dict[str, Any], list[str]]:
    """Build a draft together with the existing names the picker can offer."""
    matches = matching_presets(provider, model.id, catalog, saved)
    if len(matches) == 1:
        key = (provider, matches[0])
        data = saved[key].to_dict() if key in saved else catalog[key]
        existing = _mirror(provider, matches[0], data)
        if data.get("model") == model.id:
            existing.setdefault("context_window", model.max_context)
        return {k: v for k, v in existing.items() if v is not None}, matches
    fields: dict[str, Any] = {
        "provider": provider,
        "name": _draft_name(model.id),
        "model": model.id,
    }
    levels = tuple(model.reasoning_levels or ())
    dated = _DATED.fullmatch(model.id)
    if dated:
        sibling = next(
            (
                data
                for (owner, _), data in catalog.items()
                if owner == provider and data.get("model") == dated[1]
            ),
            None,
        )
    else:
        sibling = nearest_sibling(provider, model.id, catalog)
    if sibling is not None:
        # A dated snapshot is the same model, so its reported context wins.
        context = (
            (model.max_context or sibling.get("max_context"))
            if dated
            else (sibling.get("max_context") or model.max_context)
        )
        limits = {
            "max_context": context,
            "max_output": model.max_output or sibling.get("max_output"),
        }
        fields |= _inherit(sibling, levels)
    else:
        limits = {key: getattr(model, key) for key in ("max_context", "max_output")}
        fields |= _kind_reasoning(kind, levels)
    limits["context_window"] = model.max_context
    fields |= {key: value for key, value in limits.items() if value}
    return (
        fold_preset_fields(fields | _package_fields(provider, model, fields)),
        matches,
    )


def draft_preset(
    provider: str, model: Any, kind: str, catalog: dict, saved: dict | None = None
) -> dict[str, Any]:
    """The draft for one model in the provider catalog."""
    return draft_with_matches(provider, model, kind, catalog, saved or {})[0]


def suggest_drafts(provider: str, models: list[Any]) -> list[dict[str, Any]]:
    """Drafts in catalog order for ``provider`` (a configured backend name)."""
    kind = draft_kind(load_backends().get(provider))
    catalog = get_all_presets()
    saved = load_presets()
    return [draft_preset(provider, model, kind, catalog, saved) for model in models]


def catalog_rows(provider: str, models: list[Any]) -> list[dict[str, Any]]:
    """Discovery rows for the Settings picker: model metadata plus its draft."""
    kind = draft_kind(load_backends().get(provider))
    catalog = get_all_presets()
    saved = load_presets()
    rows = []
    for model in models:
        draft, matches = draft_with_matches(provider, model, kind, catalog, saved)
        row = {
            **asdict(model),
            "reasoning_levels": list(model.reasoning_levels or ()),
            "context_window": model.max_context,
            "has_preset": bool(matches),
            "preset": draft,
        }
        if len(matches) > 1:
            row["preset_choices"] = matches
        rows.append(row)
    return rows
