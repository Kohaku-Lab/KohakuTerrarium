"""Authentication CLI."""

import asyncio

from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login
from kohakuterrarium.llm.profiles import get_api_key, load_backends, save_api_key


def login_cli(provider: str) -> int:
    """Authenticate with a built-in or custom provider profile."""
    backends = load_backends()
    backend = backends.get(provider)
    if backend is None:
        print(f"Unknown provider: {provider}")
        return 1
    if backend.backend_type == "codex":
        return _login_codex()
    return _login_api_key(provider, backend.api_key_env)


def _login_api_key(provider: str, env_var: str) -> int:
    existing = get_api_key(provider)
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

    save_api_key(provider, key)
    print(f"\nSaved provider token for: {provider}")
    print("You can now use presets bound to this provider:")
    print("  kt model list")
    print("  kt run @kt-defaults/creatures/swe --llm <model>")
    return 0


def _login_codex() -> int:
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
        print("Tokens saved to: ~/.kohakuterrarium/codex-auth.json")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
