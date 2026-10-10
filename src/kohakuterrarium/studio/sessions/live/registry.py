"""The live-session list: every server-hosted session that should be running.

One small KVault file, ``<config_dir>/runtime/live.kvault``, separate from
the session files. A row is keyed by the session file's resolved path:

    {path, session_id, session_dir, server, added_at, boot_id, claimed_by, failed}

``session_dir`` is the hosting engine's session directory, which tells boot
which engine (shared or per user) restores it, and ``server`` which server
hosts it when several share one config dir. Rows are added when a session
goes live and removed only by a user stop / end / delete, so a server
shutdown or crash leaves them for the next boot.
"""

import os
import threading
import time
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TABLE = "live"
_lock = threading.Lock()
_open: dict[str, "LiveSessions"] = {}


def path_key(path: str | Path) -> str:
    """The row key for a session file: resolved, case-folded on case-insensitive filesystems."""
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


class LiveSessions:
    """Read and write the live-session rows of one registry file."""

    def __init__(self, file: Path) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        self.file = file
        self._kv = KVault(str(file), table=_TABLE)
        self._kv.enable_auto_pack()

    def add(
        self,
        path: str | Path,
        *,
        session_id: str,
        session_dir: str,
        boot_id: str,
        server: str | None = None,
    ) -> dict:
        key = path_key(path)
        row = {
            "path": str(Path(path).expanduser().resolve(strict=False)),
            "session_id": session_id,
            "session_dir": str(session_dir or ""),
            "server": server,
            "added_at": time.time(),
            "boot_id": boot_id,
            "claimed_by": None,
            "failed": None,
        }
        with _lock:
            prev = self._kv.get(key)
            if isinstance(prev, dict):
                # A restore re-tracks its row: keep who restored it.
                row["added_at"] = prev.get("added_at", row["added_at"])
                row["claimed_by"] = prev.get("claimed_by")
            self._kv[key] = row
        return row

    def get(self, path: str | Path) -> dict | None:
        value = self._kv.get(path_key(path))
        return value if isinstance(value, dict) else None

    def update(self, path: str | Path, **fields: Any) -> dict | None:
        key = path_key(path)
        with _lock:
            row = self._kv.get(key)
            if not isinstance(row, dict):
                return None
            row = {**row, **fields}
            self._kv[key] = row
        return row

    def remove(self, path: str | Path) -> bool:
        key = path_key(path)
        with _lock:
            if key not in self._kv:
                return False
            del self._kv[key]
        return True

    def rows(self) -> list[dict]:
        out = []
        for key in list(self._kv.keys()):
            value = self._kv.get(key)
            if isinstance(value, dict):
                out.append(value)
        return sorted(out, key=lambda r: r.get("added_at") or 0)

    def close(self) -> None:
        try:
            self._kv.close()
        except Exception:  # noqa: BLE001 - closing is best effort
            logger.debug("live registry close failed", exc_info=True)


def registry_file() -> Path:
    return config_dir() / "runtime" / "live.kvault"


def live_sessions() -> LiveSessions:
    """The process-wide handle for the current config dir's registry file."""
    file = registry_file()
    key = path_key(file)
    with _lock:
        handle = _open.get(key)
        if handle is None:
            handle = LiveSessions(file)
            _open[key] = handle
    return handle


def close_all() -> None:
    """Close every open registry handle (tests switch config dirs)."""
    with _lock:
        handles = list(_open.values())
        _open.clear()
    for handle in handles:
        handle.close()
