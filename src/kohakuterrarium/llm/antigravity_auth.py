"""Borrow consumer credentials from the official Windows agy CLI."""

import asyncio
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


class AntigravityError(ValueError):
    """A fixed, credential-free diagnostic code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Antigravity: {code}")


@dataclass(frozen=True)
class BorrowedCredential:
    """An in-memory access credential; refresh tokens are never retained."""

    access_token: str = field(repr=False)
    expires_at: float
    source: str

    def fresh(self) -> bool:
        return self.expires_at > time.time() + 60

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.access_token.encode()).hexdigest()


def parse_credential(raw: str, source: str) -> BorrowedCredential:
    """Extract the supported consumer credential without retaining its owner data."""
    try:
        if len(raw) > 65536:
            raise ValueError
        if raw.startswith("go-keyring-base64:"):
            raw = base64.b64decode(raw.split(":", 1)[1], validate=True).decode("utf-8")
        payload = json.loads(raw)
        if payload.get("auth_method") != "consumer":
            raise ValueError
        token = payload["token"]
        access = token["access_token"]
        if not isinstance(access, str) or not re.fullmatch(
            r"[A-Za-z0-9._~+/=-]+", access
        ):
            raise ValueError
        if token.get("token_type", "").lower() != "bearer":
            raise ValueError
        expiry = datetime.fromisoformat(token["expiry"].replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError
        return BorrowedCredential(access, expiry.timestamp(), source)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise AntigravityError("malformed_credential") from None


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


def read_keyring() -> str | None:
    """Read the single known Windows Credential Manager target."""
    if os.name != "nt":
        raise AntigravityError("unsupported_platform")
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


def read_sources() -> list[BorrowedCredential]:
    """Read only the agy keyring entry and its documented fallback file."""
    try:
        raw = read_keyring()
        values = [parse_credential(raw, "windows_keyring")] if raw else []
        path = Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                values.append(parse_credential(handle.read(65537), "agy_file"))
        return values
    except (OSError, UnicodeError):
        raise AntigravityError("credential_store_unavailable") from None


def load_credential() -> BorrowedCredential:
    values = read_sources()
    if not values:
        raise AntigravityError("login_required")
    if len(values) != 1:
        raise AntigravityError("ambiguous_sources")
    return values[0]


def agy_executable() -> str | None:
    return shutil.which("agy")


def refresh_lock_path() -> Path:
    return Path.home() / ".kohakuterrarium/locks/agy-refresh.lock"


async def run_agy_models() -> None:
    """Run a bounded, noninteractive refresh through the credential owner."""
    executable = agy_executable()
    if not executable:
        raise AntigravityError("agy_not_installed")
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--output-format",
            "json",
            "models",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=25)
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        if process.returncode != 0:
            raise AntigravityError("agy_refresh_failed")
    except (OSError, asyncio.TimeoutError):
        raise AntigravityError("agy_refresh_failed") from None


_refresh_tasks: dict[asyncio.AbstractEventLoop, asyncio.Task] = {}


async def _refresh(rejected: str | None) -> BorrowedCredential:
    lock = FileLock(refresh_lock_path())
    deadline = time.monotonic() + 30
    try:
        while True:
            try:
                lock.acquire()
                break
            except FileLockBusy:
                if time.monotonic() >= deadline:
                    raise AntigravityError("refresh_busy") from None
                await asyncio.sleep(0.1)
        current = await asyncio.to_thread(load_credential)
        if current.fresh() and current.fingerprint != rejected:
            return current
        await run_agy_models()
        current = await asyncio.to_thread(load_credential)
        if not current.fresh() or current.fingerprint == rejected:
            raise AntigravityError("refresh_required")
        return current
    except OSError:
        raise AntigravityError("refresh_unavailable") from None
    finally:
        lock.release()


class AgyCredentials:
    """Offline status and coordinated, owner-managed access-token renewal."""

    @staticmethod
    def status() -> dict:
        try:
            token = load_credential()
            state = "ready" if token.fresh() else "expired"
            return {
                "state": state,
                "source": token.source,
                "refresh_available": bool(agy_executable()),
            }
        except AntigravityError as exc:
            return {"state": exc.code, "refresh_available": bool(agy_executable())}

    @classmethod
    def available(cls) -> bool:
        status = cls.status()
        return status["state"] == "ready" or (
            status["state"] == "expired" and status["refresh_available"]
        )

    @staticmethod
    async def ensure_fresh(*, rejected: str | None = None) -> BorrowedCredential:
        current = await asyncio.to_thread(load_credential)
        if current.fresh() and current.fingerprint != rejected:
            return current
        loop = asyncio.get_running_loop()
        task = _refresh_tasks.get(loop)
        if task is None or task.done():
            task = loop.create_task(_refresh(rejected))
            _refresh_tasks[loop] = task

            def clear(done):
                if _refresh_tasks.get(loop) is done:
                    _refresh_tasks.pop(loop, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(clear)
        result = await asyncio.shield(task)
        if result.fingerprint == rejected:
            raise AntigravityError("refresh_required")
        return result
