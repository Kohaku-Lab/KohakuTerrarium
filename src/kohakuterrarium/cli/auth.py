"""Authentication CLI dispatcher — codex OAuth vs API key per provider."""

from kohakuterrarium.cli.identity.antigravity import login_cli as _antigravity_login
from kohakuterrarium.cli.identity.codex import login_cli as _codex_login
from kohakuterrarium.cli.identity.keys import login_with_api_key
from kohakuterrarium.llm.profiles.backend_registry import package_backend
from kohakuterrarium.studio.identity.llm.backends import get_backend


def login_cli(provider: str) -> int:
    """Authenticate with a built-in or custom provider profile."""
    backend = get_backend(provider)
    if backend is None:
        print(f"Unknown provider: {provider}")
        return 1
    # Codex uses OAuth only for the default endpoint; a custom base URL selects
    # API-key authentication consistently across bootstrap and UI availability.
    if backend.backend_type == "codex" and not backend.base_url:
        return _codex_login()
    if backend.backend_type == "google-antigravity":
        return _antigravity_login()
    descriptor = package_backend(backend.backend_type)
    if descriptor is not None and descriptor.manages_own_credentials:
        print(
            f"Provider {provider!r} (package {descriptor.package!r}) manages its "
            "own login; KohakuTerrarium stores no credentials for it."
        )
        return 1
    return login_with_api_key(provider, backend.api_key_env)
