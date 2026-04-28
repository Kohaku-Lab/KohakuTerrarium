"""CLI provider/backend management — list/add/edit/delete."""

from kohakuterrarium.cli.config_prompts import prompt as _prompt
from kohakuterrarium.cli.config_prompts import prompt_choice as _prompt_choice
from kohakuterrarium.cli.config_prompts import (
    prompt_native_tools as _prompt_native_tools,
)
from kohakuterrarium.studio.identity.llm_backends import (
    get_backend,
    list_backends,
    remove_backend,
    save_backend_record,
)


def list_cli() -> int:
    backends = list_backends()
    if not backends:
        print("No providers.")
        return 0
    print(
        f"{'Name':<24} {'Backend':<10} {'Provider name':<16} "
        f"{'Native tools':<22} {'Base URL'}"
    )
    print("-" * 110)
    for entry in sorted(backends, key=lambda b: b["name"]):
        native = ",".join(sorted(entry.get("provider_native_tools") or [])) or "-"
        provider_name = entry.get("provider_name") or "-"
        print(
            f"{entry['name']:<24} {entry['backend_type']:<10} "
            f"{provider_name:<16} {native:<22} {entry['base_url']}"
        )
    return 0


def add_or_update_cli(name: str | None = None) -> int:
    existing = get_backend(name) if name else None
    backend_name = name or _prompt("Provider name")
    if not backend_name:
        print("Provider name is required.")
        return 1
    backend_type = _prompt_choice(
        "Backend type",
        ["openai", "codex", "anthropic"],
        existing.backend_type if existing else "openai",
    )
    provider_name = _prompt(
        "Provider identity (for native-tool compatibility)",
        existing.provider_name if existing and existing.provider_name else backend_name,
    )
    native_tools = _prompt_native_tools(
        list(existing.provider_native_tools) if existing else []
    )
    try:
        save_backend_record(
            name=backend_name,
            backend_type=backend_type,
            base_url=_prompt("Base URL", existing.base_url if existing else ""),
            api_key_env=_prompt(
                "API key env", existing.api_key_env if existing else ""
            ),
            provider_name=provider_name.strip(),
            provider_native_tools=native_tools,
        )
    except ValueError as e:
        print(str(e))
        return 1
    print(f"Saved provider: {backend_name}")
    return 0


def delete_cli(name: str) -> int:
    try:
        deleted = remove_backend(name)
    except ValueError as e:
        print(str(e))
        return 1
    if not deleted:
        print(f"Provider not found: {name}")
        return 1
    print(f"Deleted provider: {name}")
    return 0
