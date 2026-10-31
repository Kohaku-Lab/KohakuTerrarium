"""Where provider API keys are kept: the config folder, or the operating system's keychain.

The keychain is optional. It needs the ``keyring`` package and a working system
store (Windows Credential Manager, macOS Keychain, a Secret Service on Linux).
"""

import importlib
import json
import os
from pathlib import Path
from types import ModuleType

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MODE_FILE = "file"
MODE_KEYCHAIN = "keychain"
MODES = (MODE_FILE, MODE_KEYCHAIN)
MODE_ENV = "KT_SECRET_STORE"
SERVICE = "KohakuTerrarium"


class SecretStoreError(RuntimeError):
    """The keychain cannot be used or a key could not be moved safely."""


def _settings_path() -> Path:
    return config_dir() / "secret-store.json"


def _index_path() -> Path:
    return config_dir() / "keychain-index.json"


def _keyring() -> ModuleType | None:
    try:
        return importlib.import_module("keyring")
    except ImportError:
        return None


def keychain_available() -> bool:
    """Whether a real system keychain can be reached."""
    keyring = _keyring()
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()
        fail = importlib.import_module("keyring.backends.fail")
    except Exception:
        return False
    return not isinstance(backend, fail.Keyring) and getattr(backend, "priority", 0) > 0


def backend_name() -> str:
    """Human-readable name of the keychain in use, or an empty string."""
    if not keychain_available():
        return ""
    return type(_keyring().get_keyring()).__name__


def configured_mode() -> str:
    """The chosen store: the environment override, else the saved choice, else the file."""
    chosen = os.environ.get(MODE_ENV, "")
    if not chosen:
        try:
            chosen = json.loads(_settings_path().read_text(encoding="utf-8")).get(
                "mode", ""
            )
        except (OSError, ValueError, AttributeError):
            chosen = ""
    return chosen if chosen in MODES else MODE_FILE


def set_configured_mode(mode: str) -> None:
    if mode not in MODES:
        raise SecretStoreError(f"unknown secret store {mode!r}")
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode}), encoding="utf-8")


def active() -> bool:
    """Whether keys are read from and written to the keychain right now."""
    if configured_mode() != MODE_KEYCHAIN:
        return False
    if keychain_available():
        return True
    logger.warning("the keychain is selected but unavailable; using the key file")
    return False


def stored_providers() -> list[str]:
    """Providers whose keys live in the keychain, as recorded by this app."""
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return (
        [name for name in data if isinstance(name, str)]
        if isinstance(data, list)
        else []
    )


def _write_index(providers: list[str]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(set(providers))), encoding="utf-8")


def get(provider: str) -> str:
    """The key stored for ``provider``; empty when none or unreadable."""
    try:
        return _keyring().get_password(SERVICE, provider) or ""
    except Exception as exc:
        logger.warning("keychain read failed", provider=provider, error=str(exc))
        return ""


def put(provider: str, key: str) -> None:
    """Store ``key`` and record the provider; raises :class:`SecretStoreError` on failure."""
    try:
        _keyring().set_password(SERVICE, provider, key)
    except Exception as exc:
        raise SecretStoreError(f"could not store the {provider} key: {exc}") from exc
    _write_index([*stored_providers(), provider])


def drop(provider: str) -> None:
    """Remove ``provider``'s key; a missing key is not an error."""
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, provider)
    except Exception as exc:
        if type(exc).__name__ != "PasswordDeleteError":
            logger.warning("keychain delete failed", provider=provider, error=str(exc))
    _write_index([name for name in stored_providers() if name != provider])


def all_keys() -> dict[str, str]:
    """Every key this app stored in the keychain."""
    keys = {name: get(name) for name in stored_providers()}
    return {name: key for name, key in keys.items() if key}
