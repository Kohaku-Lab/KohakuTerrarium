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


def list_keys_payload() -> list[dict[str, Any]]:
    """Return per-provider key status (HTTP shape)."""
    masked = list_api_keys()
    entries: list[dict[str, Any]] = []
    for name, backend in load_backends().items():
        entries.append(
            {
                "provider": name,
                "backend_type": backend.backend_type,
                "env_var": backend.api_key_env,
                "has_key": bool(get_api_key(name)),
                "masked_key": masked.get(name, ""),
                "available": _is_available(name),
                "built_in": name in {"codex", *PROVIDER_KEY_MAP.keys()},
            }
        )
    return entries


def list_keys_for_cli() -> list[dict[str, Any]]:
    """Return masked keys + env-resolution status for ``kt config key list``."""
    masked = list_api_keys()
    rows: list[dict[str, Any]] = []
    for provider, backend in sorted(load_backends().items()):
        value = masked.get(provider, "")
        env_var = backend.api_key_env or ""
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
    """Persist an API key. Raises ``ValueError`` for missing/unknown provider."""
    if not provider or not key:
        raise ValueError("Provider and key are required")
    if provider not in load_backends():
        raise LookupError(f"Provider not found: {provider}")
    save_api_key(provider, key)


def remove_key(provider: str) -> None:
    """Delete the stored key for a provider. Raises on unknown provider."""
    if provider not in load_backends():
        raise LookupError(f"Provider not found: {provider}")
    save_api_key(provider, "")


def get_existing_key(provider: str) -> str:
    """Return the currently stored key (for masked display only)."""
    return get_api_key(provider)
