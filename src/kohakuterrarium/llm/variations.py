"""Pure variation-selector machinery.

Parse and apply provider-independent preset variation selectors.
"""

from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.profile_types import LLMPreset

# Variation patches are constrained to model-facing preset fields.
_ALLOWED_VARIATION_ROOTS: set[str] = {
    "temperature",
    "reasoning_effort",
    "service_tier",
    "max_context",
    "max_output",
    "extra_body",
    "retry_policy",
}

# Bare shorthand is retained until the preset's groups can disambiguate it.
_SHORTHAND_SELECTION_KEY = "__option__"


def parse_variation_selector(selector: str) -> tuple[str, dict[str, str]]:
    """Parse a preset selector and retain unqualified shorthand for later resolution."""
    if "@" not in selector:
        return selector, {}

    base_name, raw_selector = selector.split("@", 1)
    if not base_name:
        raise ValueError("Variation selector is missing a preset/model name before '@'")
    if not raw_selector.strip():
        raise ValueError(f"Variation selector for '{base_name}' is empty")

    selections: dict[str, str] = {}
    for raw_part in raw_selector.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(f"Invalid empty variation selection in '{selector}'")
        if "=" in part:
            group, option = part.split("=", 1)
            group = group.strip()
            option = option.strip()
            if not group or not option:
                raise ValueError(
                    f"Invalid variation selection '{part}' in '{selector}'"
                )
            selections[group] = option
        else:
            if _SHORTHAND_SELECTION_KEY in selections:
                raise ValueError(
                    "Variation shorthand may only specify one option without a group"
                )
            selections[_SHORTHAND_SELECTION_KEY] = part
    return base_name, selections


def _validate_patch_target(path: str) -> None:
    root = path.split(".", 1)[0]
    if root not in _ALLOWED_VARIATION_ROOTS:
        raise ValueError(
            f"Unsupported variation patch target '{path}'. "
            f"Allowed roots: {', '.join(sorted(_ALLOWED_VARIATION_ROOTS))}"
        )


def _set_dotted_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        existing = cur.get(part)
        if existing is None:
            existing = {}
            cur[part] = existing
        if not isinstance(existing, dict):
            raise ValueError(
                f"Cannot apply variation patch '{path}': '{part}' is not an object"
            )
        cur = existing
    cur[parts[-1]] = deepcopy(value)


def apply_patch_map(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply validated dotted-path patches to a deep copy of the base preset."""
    result = deepcopy(base)
    for path, value in (patch or {}).items():
        _validate_patch_target(path)
        _set_dotted_path(result, path, value)
    return result


def normalize_variation_selections(
    selection_map: dict[str, str],
    preset: LLMPreset,
) -> dict[str, str]:
    """Resolve shorthand and validate every selected group and option."""
    groups = preset.variation_groups or {}
    selections = dict(selection_map or {})
    normalized: dict[str, str] = {}

    shorthand = selections.pop(_SHORTHAND_SELECTION_KEY, "")
    if shorthand:
        matching_groups = [
            group_name
            for group_name, options in groups.items()
            if shorthand in (options or {})
        ]
        if not matching_groups:
            raise ValueError(
                f"Unknown variation option '{shorthand}' for preset '{preset.name}'"
            )
        if len(matching_groups) > 1:
            raise ValueError(
                f"Ambiguous variation option '{shorthand}' for preset '{preset.name}'. "
                f"Specify one of: {', '.join(f'{g}={shorthand}' for g in matching_groups)}"
            )
        normalized[matching_groups[0]] = shorthand

    for group_name, option_name in selections.items():
        if group_name not in groups:
            raise ValueError(
                f"Unknown variation group '{group_name}' for preset '{preset.name}'"
            )
        group_options = groups[group_name] or {}
        if option_name not in group_options:
            raise ValueError(
                f"Unknown variation option '{option_name}' in group '{group_name}' "
                f"for preset '{preset.name}'"
            )
        normalized[group_name] = option_name

    return normalized


def apply_variation_groups(
    base: dict[str, Any],
    variation_groups: dict[str, dict[str, dict[str, Any]]],
    selections: dict[str, str],
) -> dict[str, Any]:
    """Apply selected patches while rejecting cross-group path collisions."""
    result = deepcopy(base)
    written_paths: dict[str, tuple[str, str]] = {}

    for group_name, option_name in selections.items():
        patch = ((variation_groups or {}).get(group_name) or {}).get(option_name) or {}
        for path in patch:
            _validate_patch_target(path)
            prior = written_paths.get(path)
            if prior is not None:
                prev_group, prev_option = prior
                raise ValueError(
                    f"Variation selections conflict on '{path}': "
                    f"{prev_group}={prev_option} and {group_name}={option_name}"
                )
            written_paths[path] = (group_name, option_name)
        result = apply_patch_map(result, patch)

    return result


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries without mutating either input."""
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
