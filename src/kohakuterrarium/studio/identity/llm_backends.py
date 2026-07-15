"""LLM backend (provider) CRUD — list/save/delete provider definitions.

Wraps :mod:`kohakuterrarium.llm.profiles` with plain records consumed by CLI
and HTTP adapters. The lower-level ``llm`` package remains the persistence owner;
this module validates and orchestrates Studio operations.
"""

from typing import Any

from kohakuterrarium.llm.api_keys import PROVIDER_KEY_MAP
from kohakuterrarium.llm.profiles import (
    LLMBackend,
    _is_available,
    delete_backend,
    get_api_key,
    load_backends,
    save_backend,
)

_BUILT_IN_BACKEND_NAMES = {
    "codex",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "mimo",
    "kimi-code",
    "glm-coding",
}
_SUPPORTED_BACKEND_TYPES = {"openai", "codex", "anthropic"}


def list_backends() -> list[dict[str, Any]]:
    """Return built-in and user-configured backends as Studio records."""
    return [
        {
            "name": name,
            "backend_type": backend.backend_type,
            "base_url": backend.base_url or "",
            "api_key_env": backend.api_key_env or "",
            "provider_name": backend.provider_name or "",
            "provider_native_tools": list(backend.provider_native_tools),
            "built_in": name in _BUILT_IN_BACKEND_NAMES,
            "has_token": bool(get_api_key(name)),
            "available": _is_available(name),
        }
        for name, backend in load_backends().items()
    ]


def get_backend(name: str) -> LLMBackend | None:
    return load_backends().get(name)


def save_backend_record(
    name: str,
    backend_type: str,
    base_url: str = "",
    api_key_env: str = "",
    provider_name: str = "",
    provider_native_tools: list[str] | None = None,
) -> LLMBackend:
    """Validate and persist a backend, raising ``ValueError`` for bad input."""
    if not name or not backend_type:
        raise ValueError("Name and backend type are required")
    if backend_type not in _SUPPORTED_BACKEND_TYPES:
        raise ValueError(f"Unsupported backend type: {backend_type}")
    backend = LLMBackend(
        name=name,
        backend_type=backend_type,
        base_url=base_url or "",
        api_key_env=api_key_env or "",
        provider_name=provider_name or "",
        provider_native_tools=list(provider_native_tools or []),
    )
    save_backend(backend)
    return backend


def remove_backend(name: str) -> bool:
    """Delete a backend, rejecting built-in or currently referenced records."""
    return delete_backend(name)


def is_provider_known(name: str) -> bool:
    """Return whether a provider has a backend or known API-key mapping."""
    return name in load_backends() or name in PROVIDER_KEY_MAP
