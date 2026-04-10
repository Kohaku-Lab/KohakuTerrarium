"""CLI authentication commands — login with API keys or OAuth."""

import asyncio

from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login
from kohakuterrarium.llm.profiles import (
    PROVIDER_KEY_MAP,
    get_api_key,
    save_api_key,
)


def login_cli(provider: str) -> int:
    """Authenticate with a provider."""
    if provider == "codex":
        return _login_codex()
    if provider in ("openrouter", "openai", "anthropic", "gemini", "mimo"):
        return _login_api_key(provider)
    print(f"Unknown provider: {provider}")
    return 1


def _login_api_key(provider: str) -> int:
    """Store an API key for a provider."""
    env_var = PROVIDER_KEY_MAP.get(provider, "")
    existing = get_api_key(provider)

    if existing:
        masked = f"{existing[:4]}...{existing[-4:]}" if len(existing) > 8 else "****"
        print(f"Existing {provider} key: {masked}")
        answer = input("Replace? [y/N]: ").strip().lower()
        if answer != "y":
            return 0

    print(f"Enter your {provider} API key")
    if env_var:
        print(f"(get one from the provider's dashboard, usually starts with a prefix)")
    print()

    try:
        key = input("API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        return 0

    if not key:
        print("No key provided")
        return 1

    save_api_key(provider, key)
    print(f"\nSaved {provider} API key to ~/.kohakuterrarium/api_keys.yaml")
    print(f"You can now use {provider} models:")
    print(f"  kt model list")
    print(f"  kt run @kt-defaults/creatures/swe --llm <model>")
    return 0


def _login_codex() -> int:
    """Authenticate with OpenAI Codex OAuth (ChatGPT subscription)."""

    # Check for existing tokens
    existing = CodexTokens.load()
    if existing and not existing.is_expired():
        print("Already authenticated (tokens valid).")
        print(
            f"Token path: {existing._path if hasattr(existing, '_path') else '~/.kohakuterrarium/codex-auth.json'}"
        )
        answer = input("Re-authenticate? [y/N]: ").strip().lower()
        if answer != "y":
            return 0

    print("Authenticating with OpenAI (ChatGPT subscription)...")
    print()

    try:
        asyncio.run(oauth_login())
        print()
        print("Authentication successful!")
        print(f"Tokens saved to: ~/.kohakuterrarium/codex-auth.json")
        print()
        print("You can now use auth_mode: codex-oauth in agent configs:")
        print("  controller:")
        print('    model: "gpt-4o"')
        print("    auth_mode: codex-oauth")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
