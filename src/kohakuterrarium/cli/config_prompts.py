"""Interactive prompts + formatters used by ``kt config``.

Split out of :mod:`config` to keep the command-dispatch module under the
per-file line-count guard. These are all small, side-effecty (print/input)
helpers — they intentionally have no return-value side effects beyond the
value they return, so callers compose them freely.
"""

import json
from typing import Any

from kohakuterrarium.llm.profile_types import LLMProfile

# ── Primitive prompts ──────────────────────────────────────────


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    while True:
        value = prompt(f"{label} ({'/'.join(choices)})", default)
        if value in choices:
            return value
        print(f"Please choose one of: {', '.join(choices)}")


def prompt_int(label: str, default: int) -> int:
    while True:
        value = prompt(label, str(default))
        try:
            return int(value)
        except ValueError:
            print("Please enter an integer.")


def prompt_optional_float(label: str, default: float | None) -> float | None:
    current = "" if default is None else str(default)
    while True:
        value = input(f"{label}{f' [{current}]' if current else ''}: ").strip()
        if not value:
            return default
        if value.lower() in {"none", "null", "-"}:
            return None
        try:
            return float(value)
        except ValueError:
            print("Please enter a number, blank, or 'none'.")


def prompt_optional_json(
    label: str, default: dict[str, Any] | None
) -> dict[str, Any] | None:
    current = json.dumps(default, ensure_ascii=False) if default else ""
    while True:
        value = input(f"{label}{f' [{current}]' if current else ''}: ").strip()
        if not value:
            return default or None
        if value.lower() in {"none", "null", "{}"}:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            continue
        if not isinstance(parsed, dict):
            print("extra_body must be a JSON object.")
            continue
        return parsed


def confirm(message: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{message} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def prompt_native_tools(default: list[str]) -> list[str]:
    """Interactive checkbox-style prompt for provider_native_tools.

    Presents every ``is_provider_native`` tool one-by-one with y/N,
    pre-checking any in ``default``. Accepts a shorthand where the
    user types ``all`` / ``none`` / a comma-separated list at the first
    prompt — that shortcut bypasses the per-tool loop.
    """
    from kohakuterrarium.builtins.tool_catalog import list_provider_native_tools

    available = list_provider_native_tools()
    if not available:
        return list(default)
    names = [str(entry["name"]) for entry in available]

    summary = ", ".join(
        f"{entry['name']} (providers: {','.join(entry['provider_support']) or 'any'})"
        for entry in available
    )
    print(f"  Available provider-native tools: {summary}")
    shortcut = prompt(
        "Enable (comma list / all / none / blank to pick interactively)",
        ",".join(default) if default else "",
    )
    shortcut_norm = shortcut.strip().lower()
    if shortcut_norm in {"all", "*"}:
        return list(names)
    if shortcut_norm in {"none", "-"}:
        return []
    if shortcut.strip():
        requested = [item.strip() for item in shortcut.split(",") if item.strip()]
        return [item for item in requested if item in names]

    selected: list[str] = []
    for entry in available:
        entry_name = str(entry["name"])
        pre = entry_name in default
        if confirm(f"  enable {entry_name!r}?", default=pre):
            selected.append(entry_name)
    return selected


# ── Variation-group builder ────────────────────────────────────

VARIATION_PATCH_ROOTS: tuple[str, ...] = (
    "temperature",
    "reasoning_effort",
    "service_tier",
    "max_context",
    "max_output",
    "extra_body",
)


def _parse_variation_patch_value(raw: str) -> Any:
    """Parse a scalar/JSON value for a variation patch entry.

    Accepts JSON-looking input (numbers, booleans, null, strings with quotes,
    lists, objects) and falls back to the raw string otherwise — so simple
    values like ``low`` don't need to be wrapped in quotes.
    """
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _prompt_single_variation_patch() -> dict[str, Any] | None:
    """Interactively build one option's patch map (dotted path -> value)."""
    print(
        "      Patch entries (blank path to finish). Allowed roots: "
        + ", ".join(VARIATION_PATCH_ROOTS)
    )
    patch: dict[str, Any] = {}
    while True:
        path = input("      Path: ").strip()
        if not path:
            break
        root = path.split(".", 1)[0]
        if root not in VARIATION_PATCH_ROOTS:
            print(f"      Path root must be one of: {', '.join(VARIATION_PATCH_ROOTS)}")
            continue
        raw = input(f"      Value for {path} (JSON or plain): ").strip()
        patch[path] = _parse_variation_patch_value(raw)
    return patch or None


def prompt_variation_groups(
    label: str,
    default: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Interactively build a variation_groups map.

    Supports three entry modes at the top prompt:
      - blank       -> keep existing default
      - ``none``    -> drop all groups
      - ``json``    -> paste a full JSON map (legacy / power-user path)
      - ``edit``    -> enter the interactive group/option builder
    """
    current = json.dumps(default or {}, ensure_ascii=False)
    hint = f" [{current}]" if current and default else ""
    print(
        f"{label}{hint} — type 'edit' to build interactively, 'json' to paste raw, blank to keep, 'none' to clear:"
    )
    mode = input("  > ").strip().lower()
    if not mode:
        return default or {}
    if mode in {"none", "null", "{}", "clear"}:
        return {}
    if mode == "json":
        return _prompt_variation_groups_raw(default)
    if mode != "edit":
        # Anything else — treat as a legacy JSON blob on the same line.
        try:
            parsed = json.loads(mode)
        except json.JSONDecodeError:
            print("Unrecognized input — keeping existing groups.")
            return default or {}
        if isinstance(parsed, dict):
            return parsed
        print("variation_groups must be a JSON object — keeping existing groups.")
        return default or {}

    groups: dict[str, dict[str, dict[str, Any]]] = {
        name: {opt: dict(patch) for opt, patch in options.items()}
        for name, options in (default or {}).items()
    }

    while True:
        names = list(groups.keys()) or ["(none)"]
        print(f"\n  Current groups: {', '.join(names)}")
        action = input("  [a]dd group / [r]emove group / [f]inish: ").strip().lower()
        if action in {"f", ""}:
            return groups
        if action == "r":
            if not groups:
                print("  No groups to remove.")
                continue
            target = input(
                f"    Remove which group? ({', '.join(groups.keys())}): "
            ).strip()
            if target in groups:
                del groups[target]
            else:
                print(f"    No such group: {target}")
            continue
        if action != "a":
            print("  Unknown action.")
            continue

        group_name = input("    Group name (e.g. reasoning): ").strip()
        if not group_name:
            print("    Group name is required.")
            continue
        options: dict[str, dict[str, Any]] = dict(groups.get(group_name, {}))
        while True:
            option_name = input(
                f"    Option name for '{group_name}' (blank to finish group): "
            ).strip()
            if not option_name:
                break
            patch = _prompt_single_variation_patch()
            options[option_name] = patch or {}
        if options:
            groups[group_name] = options
        else:
            print(f"    Group '{group_name}' has no options — skipping.")


def _prompt_variation_groups_raw(
    default: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Legacy raw-JSON input path for ``variation_groups``."""
    current = json.dumps(default or {}, ensure_ascii=False)
    while True:
        value = input(
            f"  Paste variation_groups JSON{f' [{current}]' if current else ''}: "
        ).strip()
        if not value:
            return default or {}
        if value.lower() in {"none", "null", "{}"}:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON: {e}")
            continue
        if not isinstance(parsed, dict):
            print(
                "  variation_groups must be a JSON object of group -> option -> patch."
            )
            continue
        invalid = False
        for group_name, options in parsed.items():
            if not isinstance(group_name, str) or not isinstance(options, dict):
                invalid = True
                break
            for option_name, patch in options.items():
                if not isinstance(option_name, str) or not isinstance(patch, dict):
                    invalid = True
                    break
            if invalid:
                break
        if invalid:
            print(
                '  variation_groups must be shaped like {"group": {"option": {"extra_body.foo": "bar"}}}.'
            )
            continue
        return parsed


# ── Formatters ─────────────────────────────────────────────────


def format_variation_groups(
    variation_groups: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    if not variation_groups:
        return []
    lines = ["Variation groups:"]
    for group_name, options in sorted(variation_groups.items()):
        option_names = ", ".join(sorted((options or {}).keys())) or "(none)"
        lines.append(f"  - {group_name}: {option_names}")
    return lines


def format_variation_examples(
    preset_name: str,
    variation_groups: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """Print a couple of ready-to-use selector examples for the user."""
    if not variation_groups:
        return []
    lines = ["Selector examples:"]
    pairs = []
    for group_name, options in sorted(variation_groups.items()):
        first_option = next(iter(sorted((options or {}).keys())), None)
        if first_option:
            pairs.append((group_name, first_option))
    if not pairs:
        return lines
    single = pairs[0]
    lines.append(f"  {preset_name}@{single[0]}={single[1]}")
    if len(pairs) > 1:
        combo = ",".join(f"{g}={o}" for g, o in pairs)
        lines.append(f"  {preset_name}@{combo}")
    return lines


def format_profile(profile: LLMProfile) -> str:
    # Imported lazily: ``profiles`` pulls in YAML / codex-auth / etc., and
    # we don't want to pay that cost just to format a line of help text.
    from kohakuterrarium.llm.profiles import _get_preset_definition

    lines = [
        f"Name:         {profile.name}",
        f"Provider:     {profile.provider}",
        f"Backend type: {profile.backend_type}",
        f"Model:        {profile.model}",
        f"Max context:  {profile.max_context}",
        f"Max output:   {profile.max_output}",
    ]
    if profile.base_url:
        lines.append(f"Base URL:     {profile.base_url}")
    if profile.api_key_env:
        lines.append(f"API key env:  {profile.api_key_env}")
    if profile.temperature is not None:
        lines.append(f"Temperature:  {profile.temperature}")
    if profile.reasoning_effort:
        lines.append(f"Reasoning:    {profile.reasoning_effort}")
    if profile.service_tier:
        lines.append(f"Service tier: {profile.service_tier}")
    if profile.selected_variations:
        lines.append(
            "Variations:   "
            + json.dumps(
                profile.selected_variations, ensure_ascii=False, sort_keys=True
            )
        )
    preset = _get_preset_definition(profile.name, profile.provider)
    if preset is not None and preset.variation_groups:
        lines.extend(format_variation_groups(preset.variation_groups))
        lines.extend(format_variation_examples(profile.name, preset.variation_groups))
    if profile.extra_body:
        lines.append(
            f"Extra body:   {json.dumps(profile.extra_body, ensure_ascii=False)}"
        )
    return "\n".join(lines)
