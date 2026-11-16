"""Read the host's agy consumer credential from its native store (blocking)."""

import ctypes
import sys
import time
from contextlib import closing
from ctypes import wintypes

try:
    import secretstorage
    from jeepney import DBusErrorResponse
    from jeepney.bus import get_bus
    from jeepney.io.blocking import DBusConnection, prep_socket
except ImportError:
    secretstorage = None
    DBusConnection = object


class AntigravityError(ValueError):
    """A fixed, credential-free diagnostic code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Antigravity: {code}")


class _WindowsCredential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _read_windows() -> str | None:
    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_WindowsCredential)),
    ]
    library.CredReadW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.POINTER(_WindowsCredential)()
    if not library.CredReadW("gemini:antigravity", 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise AntigravityError("credential_store_unavailable")
    try:
        length = pointer.contents.CredentialBlobSize
        if length > 65536:
            raise AntigravityError("malformed_credential")
        return ctypes.string_at(pointer.contents.CredentialBlob, length).decode("utf-8")
    except UnicodeError:
        raise AntigravityError("malformed_credential") from None
    finally:
        library.CredFree(pointer)


class _DeadlineConnection(DBusConnection):
    """A Secret Service connection with a total read deadline."""

    def __init__(self, sock, deadline: float):
        self.deadline = deadline
        try:
            super().__init__(sock)
        except BaseException:
            self.close()
            raise

    def send_and_get_reply(self, message, *, timeout=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.sock.settimeout(remaining)
        return super().send_and_get_reply(
            message, timeout=remaining if timeout is None else min(timeout, remaining)
        )


def _connect_linux():
    deadline = time.monotonic() + 5
    sock = prep_socket(get_bus("SESSION"), timeout=5)
    try:
        return _DeadlineConnection(sock, deadline)
    except BaseException:
        sock.close()
        raise


def _read_linux() -> str | None:
    if secretstorage is None:
        raise AntigravityError("credential_store_unavailable")
    try:
        with closing(_connect_linux()) as connection:
            try:
                collection = secretstorage.Collection(
                    connection, "/org/freedesktop/secrets/collection/login"
                )
            except secretstorage.ItemNotFoundException:
                try:
                    collection = secretstorage.get_collection_by_alias(
                        connection, "default"
                    )
                except secretstorage.ItemNotFoundException:
                    return None
            if collection.is_locked():
                raise AntigravityError("credential_store_locked")
            items = list(
                collection.search_items(
                    {"service": "gemini", "username": "antigravity"}
                )
            )
            if not items:
                return None
            if len(items) != 1:
                raise AntigravityError("ambiguous_sources")
            if items[0].is_locked():
                raise AntigravityError("credential_store_locked")
            raw = items[0].get_secret()
            if len(raw) > 65536:
                raise AntigravityError("malformed_credential")
            return raw.decode("utf-8")
    except AntigravityError:
        raise
    except secretstorage.LockedException:
        raise AntigravityError("credential_store_locked") from None
    except UnicodeError:
        raise AntigravityError("malformed_credential") from None
    except (
        secretstorage.SecretStorageException,
        DBusErrorResponse,
        OSError,
        ValueError,
        KeyError,
    ):
        raise AntigravityError("credential_store_unavailable") from None


def read_keyring() -> tuple[str | None, str]:
    """Read only the known agy entry without unlocking or creating a store."""
    match sys.platform:
        case "win32":
            return _read_windows(), "windows_keyring"
        case "linux":
            return _read_linux(), "linux_secret_service"
        case _:
            raise AntigravityError("unsupported_platform")
