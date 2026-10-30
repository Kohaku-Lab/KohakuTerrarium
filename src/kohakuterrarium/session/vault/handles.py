"""Release and inspect native KohakuVault table handles."""

from typing import Any

from kohakuvault import KVault, TextVault, VectorKVault


def dispose(handle: Any) -> bool:
    """Release one table's native connections; raise if release failed.

    Returns False when the handle closed but a background flush worker is
    still alive, so the caller cannot prove the file was released.
    """
    match handle:
        case TextVault():
            # TextVault has no close(); its native object owns the connection.
            try:
                del handle._vault
            except AttributeError:
                pass
            return True
        case KVault():
            daemon = getattr(handle, "_daemon_thread", None)
            handle.close()
            return not (daemon is not None and daemon.is_alive())
        case VectorKVault():
            handle.close()
            return True
        case _:
            handle.close()
            return True


def is_disposed(handle: Any) -> bool:
    """Whether ``handle`` no longer holds a native connection."""
    match handle:
        case TextVault():
            return not hasattr(handle, "_vault")
        case KVault():
            return bool(handle._closed)
        case VectorKVault():
            return handle._native is None
        case _:
            return bool(getattr(handle, "_closed", False))
