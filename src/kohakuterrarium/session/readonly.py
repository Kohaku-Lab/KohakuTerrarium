"""Strictly read session metadata without opening writable source state."""

import gc
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _load_meta(path: str | Path) -> dict[str, Any]:
    vault = KVault(path, table="meta", enable_wal=False)
    try:
        vault.enable_auto_pack()
        return {
            key.decode("utf-8") if isinstance(key, bytes) else str(key): vault[key]
            for key in vault.keys()
        }
    finally:
        try:
            vault.close()
        except Exception as exc:
            logger.warning("Failed to close read-only session metadata", error=str(exc))
        del vault
        gc.collect()


def read_session_meta(path: str | Path) -> dict[str, Any]:
    """Return metadata without creating WAL, SHM, schema, or status writes."""
    source = Path(path).expanduser().resolve(strict=True)
    wal = Path(f"{source}-wal")
    if not wal.exists():
        return _load_meta(f"file:{source.as_posix()}?mode=ro&immutable=1")

    tmp = Path(tempfile.mkdtemp(prefix="kt-session-read-"))
    try:
        target = tmp / source.name
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{source}{suffix}")
            if sidecar.exists():
                shutil.copy2(sidecar, Path(f"{target}{suffix}"))
        return _load_meta(target)
    finally:
        for attempt in range(4):
            try:
                shutil.rmtree(tmp)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                gc.collect()
                time.sleep(0.02 * (attempt + 1))
