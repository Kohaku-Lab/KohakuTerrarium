"""API key CRUD — list/save/delete provider keys."""

import os
from typing import Any

from kohakuterrarium.llm.api_keys import KEYS_PATH, list_api_keys
from kohakuterrarium.llm.profiles import (
    PROVIDER_KEY_MAP,
    _is_available,
    get_api_key,
    load_backends,
    save_api_key,
)

KEYS_FILE_PATH = KEYS_PATH


def _provider_credentials(
    backends: dict[str, Any] | None = None,
) -> dict[str, tuple[str, str]]:
    """Return provider -> (backend type, env var), including tool-only keys."""
    resolved_backends = load_backends() if backends is None else backends
    credentials = {
        name: (backend.backend_type, backend.api_key_env or "")
        for name, backend in resolved_backends.items()
    }
    for name, env_var in PROVIDER_KEY_MAP.items():
        credentials.setdefault(name, ("credential", env_var))
    return credentials


def list_keys_payload() -> list[dict[str, Any]]:
    """Return the HTTP-facing API-key status for each configured provider."""
    masked = list_api_keys()
    entries: list[dict[str, Any]] = []
    backends = load_backends()
    for name, (backend_type, env_var) in _provider_credentials(backends).items():
        has_key = bool(get_api_key(name))
        entries.append(
            {
                "provider": name,
                "backend_type": backend_type,
                "env_var": env_var,
                "has_key": has_key,
                "masked_key": masked.get(name, ""),
                "available": _is_available(name) if name in backends else has_key,
                "built_in": name in {"codex", *PROVIDER_KEY_MAP.keys()},
            }
        )
    return entries


def list_keys_for_cli() -> list[dict[str, Any]]:
    """Return masked keys and environment resolution for the CLI listing."""
    masked = list_api_keys()
    rows: list[dict[str, Any]] = []
    for provider, (_, env_var) in sorted(_provider_credentials().items()):
        value = masked.get(provider, "")
        if value:
            source = "stored"
        elif env_var and os.environ.get(env_var):
            source = "env"
        else:
            source = "missing"
        shown = value or ("(from env)" if source == "env" else "")
        rows.append(
            {
                "provider": provider,
                "env_var": env_var,
                "source": source,
                "shown": shown,
            }
        )
    return rows


def set_key(provider: str, key: str) -> None:
    """Persist an API key, rejecting missing values or unknown providers."""
    if not provider or not key:
        raise ValueError("Provider and key are required")
    if provider not in _provider_credentials():
        raise LookupError(f"Provider not found: {provider}")
    save_api_key(provider, key)


def remove_key(provider: str) -> None:
    """Delete a provider's stored key, rejecting unknown providers."""
    if provider not in _provider_credentials():
        raise LookupError(f"Provider not found: {provider}")
    save_api_key(provider, "")


def get_existing_key(provider: str) -> str:
    """Return the resolved provider key for masked display workflows."""
    return get_api_key(provider)
