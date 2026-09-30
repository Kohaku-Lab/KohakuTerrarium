"""Registry of session files held open by a live ``SessionStore`` in this process.

Two SQLite library copies (Python's ``sqlite3`` and KohakuVault's) must not open
one file in the same process. Readers ask this registry first and read through
the live store when it holds the file.
"""

import os
import threading
import weakref
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.fs_path import coerce_fs_path

_lock = threading.Lock()
_open: dict[str, list["weakref.ReferenceType[Any]"]] = {}


def _key(path: str | Path) -> str:
    return os.path.normcase(str(coerce_fs_path(path).resolve()))


def register_open_store(path: str | Path, store: Any) -> None:
    """Record ``store`` as holding ``path`` open."""
    with _lock:
        _open.setdefault(_key(path), []).append(weakref.ref(store))


def unregister_open_store(path: str | Path, store: Any) -> None:
    """Forget ``store``; other handles on the same path stay registered."""
    key = _key(path)
    with _lock:
        remaining = [ref for ref in _open.get(key, []) if ref() not in (None, store)]
        if remaining:
            _open[key] = remaining
        else:
            _open.pop(key, None)


def live_store_for(path: str | Path) -> Any | None:
    """Return a store that holds ``path`` open in this process, or ``None``."""
    with _lock:
        for ref in _open.get(_key(path), []):
            store = ref()
            if store is not None:
                return store
    return None
