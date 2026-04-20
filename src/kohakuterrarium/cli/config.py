import argparse
import json
import os
from pathlib import Path
from typing import Any

from kohakuterrarium.cli.auth import login_cli
from kohakuterrarium.cli.config_mcp import add_or_update_mcp, delete_mcp, list_mcp
from kohakuterrarium.llm.api_keys import KEYS_PATH, list_api_keys, save_api_key
from kohakuterrarium.llm.profiles import (
    PROFILES_PATH,
    LLMBackend,
    LLMProfile,
    LLMPreset,
    _get_preset_definition,
    delete_backend,
    delete_profile,
    get_default_model,
    get_profile,
    load_backends,
    load_profiles,
    save_backend,
    save_profile,
    set_default_model,
)


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


def _prompt_choice(label: str, choices: list[str], default: str) -> str:
    while True:
        value = _prompt(f"{label} ({'/'.join(choices)})", default)
        if value in choices:
            return value
        print(f"Please choose one of: {', '.join(choices)}")


def _prompt_int(label: str, default: int) -> int:
    while True:
        value = _prompt(label, str(default))
        try:
            return int(value)
        except ValueError:
            print("Please enter an integer.")


def _prompt_optional_float(label: str, default: float | None) -> float | None:
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


def _prompt_optional_json(
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


_VARIATION_PATCH_ROOTS = (
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
        + ", ".join(_VARIATION_PATCH_ROOTS)
    )
    patch: dict[str, Any] = {}
    while True:
        path = input("      Path: ").strip()
        if not path:
            break
        root = path.split(".", 1)[0]
        if root not in _VARIATION_PATCH_ROOTS:
            print(
                f"      Path root must be one of: {', '.join(_VARIATION_PATCH_ROOTS)}"
            )
            continue
        raw = input(f"      Value for {path} (JSON or plain): ").strip()
        patch[path] = _parse_variation_patch_value(raw)
    return patch or None


def _prompt_variation_groups(
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


def _confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _format_variation_groups(
    variation_groups: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    if not variation_groups:
        return []
    lines = ["Variation groups:"]
    for group_name, options in sorted(variation_groups.items()):
        option_names = ", ".join(sorted((options or {}).keys())) or "(none)"
        lines.append(f"  - {group_name}: {option_names}")
    return lines


def _format_variation_examples(
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


def _format_profile(profile: LLMProfile) -> str:
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
            f"Variations:   {json.dumps(profile.selected_variations, ensure_ascii=False, sort_keys=True)}"
        )
    preset = _get_preset_definition(profile.name)
    if preset is not None and preset.variation_groups:
        lines.extend(_format_variation_groups(preset.variation_groups))
        lines.extend(_format_variation_examples(profile.name, preset.variation_groups))
    if profile.extra_body:
        lines.append(
            f"Extra body:   {json.dumps(profile.extra_body, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def _config_paths() -> dict[str, Path]:
    base = Path.home() / ".kohakuterrarium"
    return {
        "home": base,
        "llm_profiles": PROFILES_PATH,
        "api_keys": KEYS_PATH,
        "mcp_servers": base / "mcp_servers.yaml",
        "ui_prefs": base / "ui_prefs.json",
    }


def _config_show() -> int:
    paths = _config_paths()
    print("KohakuTerrarium config paths")
    for name, path in paths.items():
        print(f"  {name:<12} {path}")
    return 0


def _config_path(name: str | None) -> int:
    paths = _config_paths()
    if not name:
        return _config_show()
    path = paths.get(name)
    if not path:
        print(f"Unknown config path key: {name}")
        print(f"Available: {', '.join(paths.keys())}")
        return 1
    print(path)
    return 0


def _config_edit(name: str | None) -> int:
    paths = _config_paths()
    key = name or "llm_profiles"
    path = paths.get(key)
    if not path:
        print(f"Unknown config target: {key}")
        print(f"Available: {', '.join(paths.keys())}")
        return 1
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITOR is not set.")
        print(path)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return os.system(f'{editor} "{path}"')


def _backend_list() -> int:
    backends = load_backends()
    if not backends:
        print("No providers.")
        return 0
    print(f"{'Name':<24} {'Backend Type':<12} {'Base URL'}")
    print("-" * 90)
    for name, backend in sorted(backends.items()):
        print(f"{name:<24} {backend.backend_type:<12} {backend.base_url}")
    return 0


def _backend_add_or_update(name: str | None = None) -> int:
    backends = load_backends()
    existing = backends.get(name or "")
    backend_name = name or _prompt("Provider name")
    if not backend_name:
        print("Provider name is required.")
        return 1
    backend = LLMBackend(
        name=backend_name,
        backend_type=_prompt_choice(
            "Backend type",
            ["openai", "codex", "anthropic"],
            existing.backend_type if existing else "openai",
        ),
        base_url=_prompt("Base URL", existing.base_url if existing else ""),
        api_key_env=_prompt("API key env", existing.api_key_env if existing else ""),
    )
    save_backend(backend)
    print(f"Saved provider: {backend.name}")
    return 0


def _backend_delete(name: str) -> int:
    try:
        deleted = delete_backend(name)
    except ValueError as e:
        print(str(e))
        return 1
    if not deleted:
        print(f"Provider not found: {name}")
        return 1
    print(f"Deleted provider: {name}")
    return 0


def _llm_list(include_builtins: bool = False) -> int:
    from kohakuterrarium.llm.profiles import list_all

    default_name = get_default_model()

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
        entries = list_all()
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
    print(f"  {'Name':<24} {'Provider':<14} {'Model':<32} {'Groups':<18} {'Default'}")
    print("-" * 100)
    for name, profile in sorted(profiles.items()):
        marker = "*" if name == default_name else ""
        preset = _get_preset_definition(name)
        group_summary = (
            ",".join(sorted((preset.variation_groups or {}).keys())) if preset else ""
        )
        print(
            f"  {name:<24} "
            f"{profile.provider:<14} "
            f"{profile.model:<32} "
            f"{group_summary:<18} {marker}"
        )
    print()
    print("Tip: `kt config llm list --all` to include built-in presets.")
    return 0


def _llm_show(name: str) -> int:
    profile = get_profile(name)
    if not profile:
        print(f"Preset not found: {name}")
        return 1
    print(_format_profile(profile))
    return 0


def _llm_add_or_update(name: str | None = None) -> int:
    existing = get_profile(name) if name else None
    profile_name = name or _prompt("Preset name")
    if not profile_name:
        print("Preset name is required.")
        return 1

    providers = sorted(load_backends().keys())
    provider_name = _prompt_choice(
        "Provider",
        providers,
        existing.provider if existing and existing.provider else providers[0],
    )
    model = _prompt("API model name", existing.model if existing else "")
    if not model:
        print("Model is required.")
        return 1

    existing_preset = _get_preset_definition(profile_name) if profile_name else None

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
    print(f"Saved preset: {profile.name}")
    if _confirm("Set as default model?", default=False):
        set_default_model(profile.name)
        print(f"Default model set to: {profile.name}")
    return 0


def _llm_delete(name: str) -> int:
    profile = get_profile(name)
    if not profile:
        print(f"Preset not found: {name}")
        return 1
    if not _confirm(f"Delete preset '{name}'?", default=False):
        print("Cancelled.")
        return 0
    if delete_profile(name):
        print(f"Deleted preset: {name}")
        return 0
    print(f"Preset not found: {name}")
    return 1


def _llm_default(name: str | None) -> int:
    if not name:
        default_name = get_default_model()
        print(default_name or "")
        return 0
    profile = get_profile(name)
    if not profile:
        print(f"Preset not found: {name}")
        return 1
    set_default_model(name)
    print(f"Default model set to: {name}")
    return 0


def _key_list() -> int:
    masked = list_api_keys()
    print(f"API keys file: {KEYS_PATH}")
    print()
    for provider, backend in sorted(load_backends().items()):
        value = masked.get(provider, "")
        source = (
            "stored"
            if value
            else (
                "env"
                if backend.api_key_env and os.environ.get(backend.api_key_env)
                else "missing"
            )
        )
        shown = value or ("(from env)" if source == "env" else "")
        print(f"{provider:<20} {backend.api_key_env:<24} {source:<8} {shown}")
    return 0


def _key_set(provider: str, value: str | None) -> int:
    if provider not in load_backends():
        print(f"Unknown provider: {provider}")
        return 1
    key = value or input(f"API key for {provider}: ").strip()
    if not key:
        print("Key is required.")
        return 1
    save_api_key(provider, key)
    print(f"Saved key for: {provider}")
    return 0


def _key_delete(provider: str) -> int:
    if provider not in load_backends():
        print(f"Unknown provider: {provider}")
        return 1
    if not _confirm(f"Delete stored key for '{provider}'?", default=False):
        print("Cancelled.")
        return 0
    save_api_key(provider, "")
    print(f"Deleted stored key for: {provider}")
    return 0


# ── Subparser + dispatch ────────────────────────────────────────


def add_config_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `kt config` command group."""
    parser = subparsers.add_parser(
        "config",
        help="Manage KohakuTerrarium configuration (providers, presets, keys, MCP)",
    )
    sub = parser.add_subparsers(dest="config_command")

    sub.add_parser("show", help="Show all configuration file paths")
    path_parser = sub.add_parser("path", help="Print the path of a config file")
    path_parser.add_argument("name", nargs="?", default=None)
    edit_parser = sub.add_parser("edit", help="Open a config file in $EDITOR")
    edit_parser.add_argument("name", nargs="?", default=None)

    # Provider (a.k.a. backend) management
    provider_parser = sub.add_parser(
        "provider",
        aliases=["backend"],
        help="Manage LLM providers (name, backend_type, base_url, api_key_env)",
    )
    provider_sub = provider_parser.add_subparsers(dest="config_provider_command")
    provider_sub.add_parser("list", help="List providers")
    p_add = provider_sub.add_parser("add", help="Add or update a provider")
    p_add.add_argument("name", nargs="?", default=None)
    p_edit = provider_sub.add_parser("edit", help="Edit an existing provider")
    p_edit.add_argument("name")
    p_del = provider_sub.add_parser("delete", help="Delete a provider")
    p_del.add_argument("name")

    # LLM preset management
    llm_parser = sub.add_parser(
        "llm",
        aliases=["model", "preset"],
        help="Manage LLM presets (model, provider binding, params)",
    )
    llm_sub = llm_parser.add_subparsers(dest="config_llm_command")
    l_list = llm_sub.add_parser("list", help="List presets")
    l_list.add_argument(
        "--all",
        dest="include_builtins",
        action="store_true",
        help="Include built-in presets in the listing",
    )
    l_show = llm_sub.add_parser("show", help="Show preset details")
    l_show.add_argument("name")
    l_add = llm_sub.add_parser("add", help="Add or update a preset")
    l_add.add_argument("name", nargs="?", default=None)
    l_edit = llm_sub.add_parser("edit", help="Edit an existing preset")
    l_edit.add_argument("name")
    l_del = llm_sub.add_parser("delete", help="Delete a preset")
    l_del.add_argument("name")
    l_def = llm_sub.add_parser("default", help="Get or set the default model")
    l_def.add_argument("name", nargs="?", default=None)

    # API key management
    key_parser = sub.add_parser("key", help="Manage stored API keys")
    key_sub = key_parser.add_subparsers(dest="config_key_command")
    key_sub.add_parser("list", help="List providers with key status")
    k_set = key_sub.add_parser("set", help="Set the API key for a provider")
    k_set.add_argument("provider")
    k_set.add_argument("value", nargs="?", default=None)
    k_del = key_sub.add_parser("delete", help="Delete the stored key for a provider")
    k_del.add_argument("provider")

    # Login passthrough — `kt config login <provider>` mirrors `kt login`
    login_parser = sub.add_parser(
        "login", help="Authenticate with a provider (OAuth or API key)"
    )
    login_parser.add_argument("provider")

    # MCP server management
    mcp_parser = sub.add_parser("mcp", help="Manage MCP servers")
    mcp_sub = mcp_parser.add_subparsers(dest="config_mcp_command")
    mcp_sub.add_parser("list", help="List MCP servers")
    m_add = mcp_sub.add_parser("add", help="Add or update an MCP server")
    m_add.add_argument("name", nargs="?", default=None)
    m_edit = mcp_sub.add_parser("edit", help="Edit an existing MCP server")
    m_edit.add_argument("name")
    m_del = mcp_sub.add_parser("delete", help="Delete an MCP server")
    m_del.add_argument("name")


def _dispatch_provider(args: argparse.Namespace) -> int:
    sub = getattr(args, "config_provider_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _backend_list()
        case "add":
            return _backend_add_or_update(name)
        case "edit":
            return _backend_add_or_update(name)
        case "delete":
            return _backend_delete(name)
    print("Usage: kt config provider {list|add|edit|delete}")
    return 1


def _dispatch_llm(args: argparse.Namespace) -> int:
    sub = getattr(args, "config_llm_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _llm_list(include_builtins=getattr(args, "include_builtins", False))
        case "show":
            return _llm_show(name) if name else (print("name required") or 1)
        case "add":
            return _llm_add_or_update(name)
        case "edit":
            return _llm_add_or_update(name)
        case "delete":
            return _llm_delete(name) if name else (print("name required") or 1)
        case "default":
            return _llm_default(name)
    print("Usage: kt config llm {list|show|add|edit|delete|default}")
    return 1


def _dispatch_key(args: argparse.Namespace) -> int:
    sub = getattr(args, "config_key_command", None) or "list"
    provider = getattr(args, "provider", None)
    match sub:
        case "list":
            return _key_list()
        case "set":
            if not provider:
                print("provider required")
                return 1
            return _key_set(provider, getattr(args, "value", None))
        case "delete":
            if not provider:
                print("provider required")
                return 1
            return _key_delete(provider)
    print("Usage: kt config key {list|set|delete}")
    return 1


def _dispatch_mcp(args: argparse.Namespace) -> int:
    sub = getattr(args, "config_mcp_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return list_mcp(_config_paths())
        case "add":
            return add_or_update_mcp(name, _prompt)
        case "edit":
            return add_or_update_mcp(name, _prompt)
        case "delete":
            return delete_mcp(name) if name else (print("name required") or 1)
    print("Usage: kt config mcp {list|add|edit|delete}")
    return 1


def config_cli(args: argparse.Namespace) -> int:
    """Entry point for the `kt config` command group."""
    command = getattr(args, "config_command", None)
    match command:
        case None | "show":
            return _config_show()
        case "path":
            return _config_path(getattr(args, "name", None))
        case "edit":
            return _config_edit(getattr(args, "name", None))
        case "provider" | "backend":
            return _dispatch_provider(args)
        case "llm" | "model" | "preset":
            return _dispatch_llm(args)
        case "key":
            return _dispatch_key(args)
        case "login":
            return login_cli(getattr(args, "provider", ""))
        case "mcp":
            return _dispatch_mcp(args)
    print(
        "Usage: kt config {show|path|edit|provider|llm|key|login|mcp} ...",
    )
    return 1
