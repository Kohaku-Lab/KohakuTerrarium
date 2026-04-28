"""CLI LLM preset management — list/show/add/edit/delete/default."""

from typing import Any

from kohakuterrarium.cli.config_prompts import confirm as _confirm
from kohakuterrarium.cli.config_prompts import format_profile as _format_profile
from kohakuterrarium.cli.config_prompts import prompt as _prompt
from kohakuterrarium.cli.config_prompts import prompt_choice as _prompt_choice
from kohakuterrarium.cli.config_prompts import prompt_int as _prompt_int
from kohakuterrarium.cli.config_prompts import (
    prompt_optional_float as _prompt_optional_float,
)
from kohakuterrarium.cli.config_prompts import (
    prompt_optional_json as _prompt_optional_json,
)
from kohakuterrarium.cli.config_prompts import (
    prompt_variation_groups as _prompt_variation_groups,
)
from kohakuterrarium.llm.profiles import (
    PROFILES_PATH,
    LLMPreset,
    load_backends,
    load_profiles,
    save_profile,
)
from kohakuterrarium.studio.identity.llm_default import (
    get_default,
    resolve_and_set_default,
    set_default,
)
from kohakuterrarium.studio.identity.llm_profiles import (
    get_preset_definition,
    get_profile_for_identifier,
    list_all_models,
    remove_profile,
    split_identifier,
)


def list_cli(include_builtins: bool = False) -> int:
    default_name = get_default()

    def _print_row(entry: dict[str, Any]) -> None:
        marker = "*" if entry["name"] == default_name else ""
        group_summary = ",".join(sorted((entry.get("variation_groups") or {}).keys()))
        avail = "✓" if entry.get("available") else "·"
        print(
            f"{avail} {entry['name']:<24} "
            f"{entry['provider']:<14} "
            f"{entry['model']:<32} "
            f"{group_summary:<18} {marker}"
        )

    if include_builtins:
        entries = list_all_models()
        print(f"Profiles file: {PROFILES_PATH}")
        print()
        print(
            f"  {'Name':<24} {'Provider':<14} {'Model':<32} {'Groups':<18} {'Default'}"
        )
        print("-" * 100)
        user_entries = [e for e in entries if e.get("source") == "user"]
        builtin_entries = [e for e in entries if e.get("source") != "user"]
        if user_entries:
            print("# User presets")
            for entry in sorted(user_entries, key=lambda e: e["name"]):
                _print_row(entry)
            print()
        if builtin_entries:
            print("# Built-in presets")
            for entry in sorted(
                builtin_entries, key=lambda e: (e["provider"], e["name"])
            ):
                _print_row(entry)
        print()
        print("Legend: ✓ = API key/OAuth configured   · = not available   * = default")
        return 0

    profiles = load_profiles()
    if not profiles:
        print("No user-defined LLM presets.")
        print(f"Profiles file: {PROFILES_PATH}")
        print()
        print("Tip: `kt config llm list --all` to include built-in presets.")
        return 0
    print(f"Profiles file: {PROFILES_PATH}")
    print()
    print(f"  {'Provider/Name':<36} {'Model':<32} {'Groups':<18} {'Default'}")
    print("-" * 100)
    for (provider, name), profile in sorted(profiles.items()):
        identifier = f"{provider}/{name}"
        marker = "*" if default_name in {identifier, name} else ""
        preset = get_preset_definition(name, provider)
        group_summary = (
            ",".join(sorted((preset.variation_groups or {}).keys())) if preset else ""
        )
        print(
            f"  {identifier:<36} "
            f"{profile.model:<32} "
            f"{group_summary:<18} {marker}"
        )
    print()
    print("Tip: `kt config llm list --all` to include built-in presets.")
    print("Tip: Reference models as 'provider/name' (e.g. 'codex/gpt-5.4').")
    return 0


def show_cli(name: str) -> int:
    try:
        profile = get_profile_for_identifier(name)
    except ValueError as e:
        print(str(e))
        return 1
    if not profile:
        print(f"Preset not found: {name}")
        return 1
    print(_format_profile(profile))
    return 0


def add_or_update_cli(name: str | None = None) -> int:
    arg_provider, arg_name = split_identifier(name) if name else ("", "")
    existing = None
    if arg_name:
        try:
            existing = get_profile_for_identifier(name or "")
        except ValueError:
            existing = None
    profile_name = arg_name or _prompt("Preset name")
    if not profile_name:
        print("Preset name is required.")
        return 1

    providers = sorted(load_backends().keys())
    default_prov = arg_provider or (
        existing.provider if existing and existing.provider else providers[0]
    )
    provider_name = _prompt_choice("Provider", providers, default_prov)
    model = _prompt("API model name", existing.model if existing else "")
    if not model:
        print("Model is required.")
        return 1

    existing_preset = (
        get_preset_definition(profile_name, provider_name) if profile_name else None
    )

    profile = LLMPreset(
        name=profile_name,
        model=model,
        provider=provider_name,
        max_context=_prompt_int(
            "Max context", existing.max_context if existing else 128000
        ),
        max_output=_prompt_int(
            "Max output", existing.max_output if existing else 16384
        ),
        temperature=_prompt_optional_float(
            "Temperature", existing.temperature if existing else None
        ),
        reasoning_effort=_prompt(
            "Reasoning effort", existing.reasoning_effort if existing else ""
        ),
        service_tier=_prompt("Service tier", existing.service_tier if existing else ""),
        extra_body=_prompt_optional_json(
            "Extra body JSON", existing.extra_body if existing else None
        )
        or {},
        variation_groups=_prompt_variation_groups(
            "Variation groups JSON",
            existing_preset.variation_groups if existing_preset else None,
        ),
    )
    save_profile(profile)
    identifier = f"{profile.provider}/{profile.name}"
    print(f"Saved preset: {identifier}")
    if _confirm("Set as default model?", default=False):
        set_default(identifier)
        print(f"Default model set to: {identifier}")
    return 0


def delete_cli(name: str) -> int:
    try:
        profile = get_profile_for_identifier(name)
    except ValueError as e:
        print(str(e))
        return 1
    if not profile:
        print(f"Preset not found: {name}")
        return 1
    if not _confirm(
        f"Delete preset '{profile.provider}/{profile.name}'?", default=False
    ):
        print("Cancelled.")
        return 0
    if remove_profile(profile.name, profile.provider):
        print(f"Deleted preset: {profile.provider}/{profile.name}")
        return 0
    print(f"Preset not found: {name}")
    return 1


def default_cli(name: str | None) -> int:
    if not name:
        print(get_default() or "")
        return 0
    identifier, error = resolve_and_set_default(name)
    if error:
        print(error)
        return 1
    print(f"Default model set to: {identifier}")
    return 0
