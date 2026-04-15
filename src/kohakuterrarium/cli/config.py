import argparse
import json
import os
from pathlib import Path
from typing import Any


from kohakuterrarium.api.routes.settings import _load_mcp_config, _save_mcp_config
from kohakuterrarium.llm.api_keys import (
    KEYS_PATH,
    PROVIDER_KEY_MAP,
    list_api_keys,
    save_api_key,
)
from kohakuterrarium.llm.profiles import (
    PROFILES_PATH,
    LLMProfile,
    delete_profile,
    get_default_model,
    get_profile,
    load_profiles,
    save_profile,
    set_default_model,
)


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


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


def _confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _format_profile(profile: LLMProfile) -> str:
    lines = [
        f"Name:        {profile.name}",
        f"Provider:    {profile.provider}",
        f"Model:       {profile.model}",
        f"Max context: {profile.max_context}",
        f"Max output:  {profile.max_output}",
    ]
    if profile.base_url:
        lines.append(f"Base URL:    {profile.base_url}")
    if profile.api_key_env:
        lines.append(f"API key env: {profile.api_key_env}")
    if profile.temperature is not None:
        lines.append(f"Temperature: {profile.temperature}")
    if profile.reasoning_effort:
        lines.append(f"Reasoning:   {profile.reasoning_effort}")
    if profile.service_tier:
        lines.append(f"Service tier:{profile.service_tier}")
    if profile.extra_body:
        lines.append(
            f"Extra body:  {json.dumps(profile.extra_body, ensure_ascii=False)}"
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


def _llm_list() -> int:
    profiles = load_profiles()
    default_name = get_default_model()
    if not profiles:
        print("No user-defined LLM profiles.")
        print(f"Profiles file: {PROFILES_PATH}")
        return 0
    print(f"Profiles file: {PROFILES_PATH}")
    print()
    print(f"{'Name':<24} {'Provider':<12} {'Model':<40} {'Default'}")
    print("-" * 90)
    for name, profile in sorted(profiles.items()):
        marker = "*" if name == default_name else ""
        print(f"{name:<24} {profile.provider:<12} {profile.model:<40} {marker}")
    return 0


def _llm_show(name: str) -> int:
    profile = get_profile(name)
    if not profile:
        print(f"Profile not found: {name}")
        return 1
    print(_format_profile(profile))
    return 0


def _llm_add_or_update(name: str | None = None) -> int:
    existing = get_profile(name) if name else None
    profile_name = name or _prompt("Profile name")
    if not profile_name:
        print("Profile name is required.")
        return 1

    provider = _prompt("Provider", existing.provider if existing else "openai")
    model = _prompt("Model", existing.model if existing else "")
    if not model:
        print("Model is required.")
        return 1

    profile = LLMProfile(
        name=profile_name,
        provider=provider,
        model=model,
        base_url=_prompt("Base URL", existing.base_url if existing else ""),
        api_key_env=_prompt("API key env", existing.api_key_env if existing else ""),
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
    )
    save_profile(profile)
    print(f"Saved profile: {profile.name}")
    if _confirm("Set as default model?", default=False):
        set_default_model(profile.name)
        print(f"Default model set to: {profile.name}")
    return 0


def _llm_delete(name: str) -> int:
    profile = get_profile(name)
    if not profile:
        print(f"Profile not found: {name}")
        return 1
    if not _confirm(f"Delete profile '{name}'?", default=False):
        print("Cancelled.")
        return 0
    if delete_profile(name):
        print(f"Deleted profile: {name}")
        return 0
    print(f"Profile not found: {name}")
    return 1


def _llm_default(name: str | None) -> int:
    if not name:
        default_name = get_default_model()
        print(default_name or "")
        return 0
    profile = get_profile(name)
    if not profile:
        print(f"Profile/preset not found: {name}")
        return 1
    set_default_model(name)
    print(f"Default model set to: {name}")
    return 0


def _key_list() -> int:
    masked = list_api_keys()
    print(f"API keys file: {KEYS_PATH}")
    print()
    providers = sorted(PROVIDER_KEY_MAP.keys())
    for provider in providers:
        env_var = PROVIDER_KEY_MAP[provider]
        value = masked.get(provider, "")
        source = (
            "stored" if value else ("env" if os.environ.get(env_var) else "missing")
        )
        shown = value or ("(from env)" if source == "env" else "")
        print(f"{provider:<12} {env_var:<24} {source:<8} {shown}")
    return 0


def _key_set(provider: str, value: str | None) -> int:
    if provider not in PROVIDER_KEY_MAP:
        print(f"Unknown provider: {provider}")
        print(f"Available: {', '.join(sorted(PROVIDER_KEY_MAP.keys()))}")
        return 1
    key = value or input(f"API key for {provider}: ").strip()
    if not key:
        print("Key is required.")
        return 1
    save_api_key(provider, key)
    print(f"Saved key for: {provider}")
    return 0


def _key_delete(provider: str) -> int:
    if provider not in PROVIDER_KEY_MAP:
        print(f"Unknown provider: {provider}")
        return 1
    if not _confirm(f"Delete stored key for '{provider}'?", default=False):
        print("Cancelled.")
        return 0
    save_api_key(provider, "")
    print(f"Deleted stored key for: {provider}")
    return 0


def _mcp_list() -> int:
    servers = _load_mcp_config()
    path = _config_paths()["mcp_servers"]
    print(f"MCP config file: {path}")
    if not servers:
        print("No MCP servers configured.")
        return 0
    print()
    for server in servers:
        print(f"- {server.get('name', '')}")
        print(f"  transport: {server.get('transport', 'stdio')}")
        if server.get("command"):
            print(f"  command:   {server.get('command', '')}")
        if server.get("args"):
            print(f"  args:      {server.get('args', [])}")
        if server.get("url"):
            print(f"  url:       {server.get('url', '')}")
        if server.get("env"):
            print(f"  env keys:  {list((server.get('env') or {}).keys())}")
    return 0


def _prompt_mcp(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    name = _prompt("Name", existing.get("name", ""))
    transport = _prompt("Transport", existing.get("transport", "stdio"))
    command = _prompt("Command", existing.get("command", ""))
    args_raw = _prompt(
        "Args JSON array", json.dumps(existing.get("args", []), ensure_ascii=False)
    )
    env_raw = _prompt(
        "Env JSON object", json.dumps(existing.get("env", {}), ensure_ascii=False)
    )
    url = _prompt("URL", existing.get("url", ""))

    try:
        args = json.loads(args_raw) if args_raw else []
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid args JSON: {e}")
    if not isinstance(args, list):
        raise ValueError("Args must be a JSON array")

    try:
        env = json.loads(env_raw) if env_raw else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid env JSON: {e}")
    if not isinstance(env, dict):
        raise ValueError("Env must be a JSON object")

    if not name:
        raise ValueError("Name is required")

    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
    }


def _mcp_add_or_update(name: str | None = None) -> int:
    servers = _load_mcp_config()
    existing = None
    if name:
        for server in servers:
            if server.get("name") == name:
                existing = server
                break
    try:
        new_server = _prompt_mcp(existing)
    except ValueError as e:
        print(e)
        return 1
    servers = [s for s in servers if s.get("name") != new_server["name"]]
    servers.append(new_server)
    _save_mcp_config(servers)
    print(f"Saved MCP server: {new_server['name']}")
    return 0


def _mcp_delete(name: str) -> int:
    servers = _load_mcp_config()
    if not any(s.get("name") == name for s in servers):
        print(f"MCP server not found: {name}")
        return 1
    if not _confirm(f"Delete MCP server '{name}'?", default=False):
        print("Cancelled.")
        return 0
    _save_mcp_config([s for s in servers if s.get("name") != name])
    print(f"Deleted MCP server: {name}")
    return 0


def config_cli(args: argparse.Namespace) -> int:
    sub = getattr(args, "config_command", None)
    if sub == "show" or sub is None:
        return _config_show()
    if sub == "path":
        return _config_path(getattr(args, "name", None))
    if sub == "edit":
        return _config_edit(getattr(args, "name", None))

    if sub == "llm":
        action = getattr(args, "config_llm_command", None)
        if action == "list" or action is None:
            return _llm_list()
        if action == "show":
            return _llm_show(args.name)
        if action == "add":
            return _llm_add_or_update()
        if action == "update":
            return _llm_add_or_update(args.name)
        if action == "delete":
            return _llm_delete(args.name)
        if action == "default":
            return _llm_default(getattr(args, "name", None))

    if sub == "key":
        action = getattr(args, "config_key_command", None)
        if action == "list" or action is None:
            return _key_list()
        if action == "set":
            return _key_set(args.provider, getattr(args, "value", None))
        if action == "delete":
            return _key_delete(args.provider)

    if sub == "mcp":
        action = getattr(args, "config_mcp_command", None)
        if action == "list" or action is None:
            return _mcp_list()
        if action == "add":
            return _mcp_add_or_update()
        if action == "update":
            return _mcp_add_or_update(args.name)
        if action == "delete":
            return _mcp_delete(args.name)

    print("Usage: kt config [show|path|edit|llm|key|mcp] ...")
    return 0


def add_config_subparser(subparsers) -> None:
    config_parser = subparsers.add_parser(
        "config", help="Manage ~/.kohakuterrarium configuration"
    )
    config_sub = config_parser.add_subparsers(dest="config_command")

    config_sub.add_parser("show", help="Show important config file locations")
    path_parser = config_sub.add_parser("path", help="Print a config file path")
    path_parser.add_argument(
        "name",
        nargs="?",
        choices=["home", "llm_profiles", "api_keys", "mcp_servers", "ui_prefs"],
        help="Named config path",
    )
    edit_parser = config_sub.add_parser("edit", help="Open a config file in $EDITOR")
    edit_parser.add_argument(
        "name",
        nargs="?",
        choices=["llm_profiles", "api_keys", "mcp_servers", "ui_prefs"],
        help="Named config target (default: llm_profiles)",
    )

    llm_parser = config_sub.add_parser("llm", help="Manage user-defined LLM profiles")
    llm_sub = llm_parser.add_subparsers(dest="config_llm_command")
    llm_sub.add_parser("list", help="List user-defined profiles")
    llm_show = llm_sub.add_parser("show", help="Show a profile")
    llm_show.add_argument("name")
    llm_sub.add_parser("add", help="Interactively add a profile")
    llm_update = llm_sub.add_parser("update", help="Interactively update a profile")
    llm_update.add_argument("name")
    llm_delete = llm_sub.add_parser("delete", help="Delete a profile")
    llm_delete.add_argument("name")
    llm_default = llm_sub.add_parser("default", help="Show or set default model")
    llm_default.add_argument("name", nargs="?")

    key_parser = config_sub.add_parser("key", help="Manage stored API keys")
    key_sub = key_parser.add_subparsers(dest="config_key_command")
    key_sub.add_parser("list", help="List stored/env-backed provider keys")
    key_set = key_sub.add_parser("set", help="Store an API key")
    key_set.add_argument("provider", choices=sorted(PROVIDER_KEY_MAP.keys()))
    key_set.add_argument("value", nargs="?")
    key_delete = key_sub.add_parser("delete", help="Delete a stored API key")
    key_delete.add_argument("provider", choices=sorted(PROVIDER_KEY_MAP.keys()))

    mcp_parser = config_sub.add_parser("mcp", help="Manage global MCP server config")
    mcp_sub = mcp_parser.add_subparsers(dest="config_mcp_command")
    mcp_sub.add_parser("list", help="List MCP servers")
    mcp_sub.add_parser("add", help="Interactively add an MCP server")
    mcp_update = mcp_sub.add_parser("update", help="Interactively update an MCP server")
    mcp_update.add_argument("name")
    mcp_delete = mcp_sub.add_parser("delete", help="Delete an MCP server")
    mcp_delete.add_argument("name")
