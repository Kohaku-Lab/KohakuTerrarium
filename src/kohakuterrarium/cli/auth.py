"""Authentication CLI dispatcher — codex OAuth vs API key per provider."""

from kohakuterrarium.cli.identity_codex import login_cli as _codex_login
from kohakuterrarium.cli.identity_keys import login_with_api_key
from kohakuterrarium.studio.identity.llm_backends import get_backend


def login_cli(provider: str) -> int:
    """Authenticate with a built-in or custom provider profile."""
    backend = get_backend(provider)
    if backend is None:
        print(f"Unknown provider: {provider}")
        return 1
    if backend.backend_type == "codex":
        return _codex_login()
    return login_with_api_key(provider, backend.api_key_env)
