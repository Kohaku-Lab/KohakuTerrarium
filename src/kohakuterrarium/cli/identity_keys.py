"""CLI API key management — list/set/delete + login passthrough."""

from kohakuterrarium.cli.config_prompts import confirm as _confirm
from kohakuterrarium.studio.identity.api_keys import (
    KEYS_FILE_PATH,
    get_existing_key,
    list_keys_for_cli,
    remove_key,
    set_key,
)


def list_cli() -> int:
    print(f"API keys file: {KEYS_FILE_PATH}")
    print()
    rows = list_keys_for_cli()
    for row in rows:
        print(
            f"{row['provider']:<20} {row['env_var']:<24} "
            f"{row['source']:<8} {row['shown']}"
        )
    return 0


def set_cli(provider: str, value: str | None) -> int:
    key = value or input(f"API key for {provider}: ").strip()
    if not key:
        print("Key is required.")
        return 1
    try:
        set_key(provider, key)
    except LookupError:
        print(f"Unknown provider: {provider}")
        return 1
    except ValueError as e:
        print(str(e))
        return 1
    print(f"Saved key for: {provider}")
    return 0


def delete_cli(provider: str) -> int:
    if not _confirm(f"Delete stored key for '{provider}'?", default=False):
        print("Cancelled.")
        return 0
    try:
        remove_key(provider)
    except LookupError:
        print(f"Unknown provider: {provider}")
        return 1
    print(f"Deleted stored key for: {provider}")
    return 0


def login_with_api_key(provider: str, env_var: str) -> int:
    """Interactive ``kt login <provider>`` for API-key providers."""
    existing = get_existing_key(provider)
    if existing:
        masked = f"{existing[:4]}...{existing[-4:]}" if len(existing) > 8 else "****"
        print(f"Existing {provider} key: {masked}")
        answer = input("Replace? [y/N]: ").strip().lower()
        if answer != "y":
            return 0

    print(f"Enter token/API key for provider '{provider}'")
    if env_var:
        print(f"Environment fallback: {env_var}")
    print()

    try:
        key = input("API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        return 0

    if not key:
        print("No key provided")
        return 1

    try:
        set_key(provider, key)
    except LookupError as e:
        print(str(e))
        return 1
    except ValueError as e:
        print(str(e))
        return 1
    print(f"\nSaved provider token for: {provider}")
    print("You can now use presets bound to this provider:")
    print("  kt model list")
    print("  kt run @kt-biome/creatures/swe --llm <model>")
    return 0
