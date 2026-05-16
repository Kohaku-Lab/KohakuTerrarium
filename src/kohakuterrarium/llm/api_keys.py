"""
API key storage and retrieval.

Keys are stored in ~/.kohakuterrarium/api_keys.yaml
Format: { openrouter: "sk-or-...", openai: "sk-...", anthropic: "sk-ant-...", gemini: "AI..." }

Resolution order in :func:`get_api_key`:

1. **Registered resolver** (see :func:`register_api_key_resolver`).
   Lab workers install a resolver that reads from a pre-populated
   :class:`IdentityCache`, so worker creatures making LLM calls
   transparently route through the controller's host-canonical
   identity store.  Standalone Studio never registers one and falls
   straight through to the local file.
2. Stored key in ``~/.kohakuterrarium/api_keys.yaml``.
3. Environment variable.
4. Empty string (not found).
"""

import os
from collections.abc import Callable
from pathlib import Path

import yaml

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Import-time defaults — kept for back-compat with callers that import
# these names for *display* (``cli/identity_keys.py``, the studio
# identity routes).  The actual read / write paths go through
# :func:`_keys_path`, which resolves ``config_dir()`` fresh on every
# call so ``KT_CONFIG_DIR`` (test isolation, operator re-homing) always
# wins — a module constant computed once at import would not.
KT_DIR = Path.home() / ".kohakuterrarium"
KEYS_PATH = KT_DIR / "api_keys.yaml"


def _keys_path() -> Path:
    """The live ``api_keys.yaml`` path, honouring ``KT_CONFIG_DIR``."""
    return config_dir() / "api_keys.yaml"


# Maps provider short names to env var names (for fallback)
PROVIDER_KEY_MAP: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mimo": "MIMO_API_KEY",
}

# Process-wide sync resolver hook.  ``Callable[[str], str]`` — given a
# provider name (already normalised), returns a key or ``""``.  Set by
# :func:`register_api_key_resolver` and cleared by
# :func:`clear_api_key_resolver`.  Single slot: each process has at
# most one active resolver (workers install one; the host doesn't).
_resolver: Callable[[str], str] | None = None


def register_api_key_resolver(resolver: Callable[[str], str]) -> None:
    """Install a sync resolver consulted before the file/env fallback.

    Designed for the worker side of multi-node mode: the worker
    pre-fetches keys via :class:`IdentityCache` at spawn time, then
    registers a resolver that does a sync dict lookup.  See
    :class:`kohakuterrarium.laboratory.identity_cache.IdentityCache`.
    """
    global _resolver
    _resolver = resolver


def clear_api_key_resolver() -> None:
    """Remove any installed resolver.  Idempotent."""
    global _resolver
    _resolver = None


def save_api_key(provider: str, key: str) -> None:
    """Save an API key for a provider."""
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = _load_api_keys()
    keys[provider] = key
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(keys, f, default_flow_style=False)
    logger.info("API key saved", provider=provider)


def get_api_key(provider_or_env: str) -> str:
    """Get an API key by provider name or env var name.

    Resolution order:
      0. Registered resolver (lab worker → IdentityCache).  When a
         resolver is installed (worker mode), this is the AUTHORITATIVE
         source — falling through to the worker's local file / env on
         a resolver miss would silently leak whatever credentials the
         worker operator happens to have locally and violate the
         "host-canonical identity" design (management-wiring.md §
         studio.identity).  So: resolver miss in worker mode returns
         ``""`` immediately.
      1. Stored key in ~/.kohakuterrarium/api_keys.yaml (standalone /
         no-resolver only).
      2. Environment variable (standalone / no-resolver only).
      3. Empty string (not found).
    """
    # Normalize: env var name -> provider name
    provider = provider_or_env
    for prov, env in PROVIDER_KEY_MAP.items():
        if provider_or_env == env:
            provider = prov
            break

    # 0. Registered resolver — local-first IdentityCache (worker mode).
    #    Reads the worker's OWN api_keys.yaml + env first, then falls
    #    back to whatever the host shared.  See
    #    :meth:`IdentityCache.sync_api_key` for the resolution order.
    if _resolver is not None:
        try:
            key = _resolver(provider)
        except Exception:  # pragma: no cover - defensive
            logger.exception("api-key resolver raised; treating as miss")
            key = ""
        if key:
            return key
        # Resolver covered both worker-local AND host caches; a miss
        # here means neither side has the key.  Surface as a warning so
        # the operator knows where to set it.
        logger.warning(
            "api-key resolver returned empty; set the key on this "
            "worker (KT_CONFIG_DIR/api_keys.yaml) OR on the host "
            "identity store (POST /api/settings/keys)",
            provider=provider,
        )
        return ""

    # Standalone path (no resolver installed): file → env → empty.
    keys = _load_api_keys()
    if provider in keys and keys[provider]:
        return keys[provider]
    env_var = PROVIDER_KEY_MAP.get(provider, provider_or_env)
    key = os.environ.get(env_var, "")
    if key:
        return key
    if provider_or_env != env_var:
        key = os.environ.get(provider_or_env, "")
    return key


def list_api_keys() -> dict[str, str]:
    """List stored API keys (masked)."""
    keys = _load_api_keys()
    masked = {}
    for provider, key in keys.items():
        if key and len(key) > 8:
            masked[provider] = f"{key[:4]}...{key[-4:]}"
        elif key:
            masked[provider] = "****"
    return masked


def _load_api_keys() -> dict[str, str]:
    """Load API keys from file."""
    path = _keys_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug("Failed to load API keys file", error=str(e))
        return {}
