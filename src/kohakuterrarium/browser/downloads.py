"""Saving page downloads: safe unique file names in the session's downloads directory."""

import asyncio
import os
import re
import secrets
import unicodedata
from pathlib import Path

from kohakuterrarium.browser.protocol import DownloadDriver
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SAVE_TIMEOUT_S = 300.0
CANCEL_TIMEOUT_S = 5.0
MAX_DOWNLOADS = 50
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
NUMBERED_TRIES = 5
RANDOM_TRIES = 3
MAX_NAME_CHARS = 120
FALLBACK_NAME = "download"

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?")


def safe_filename(suggested: str) -> str:
    """A plain file name from a page-suggested one: no directories, no reserved names."""
    name = unicodedata.normalize("NFC", suggested or "")
    name = re.split(r"[/\\]", name)[-1]
    name = _UNSAFE.sub("_", name).strip().strip(".").strip()
    if not name or _RESERVED.fullmatch(name):
        name = f"{FALLBACK_NAME}_{name}" if name else FALLBACK_NAME
    if len(name) > MAX_NAME_CHARS:
        stem, ext = os.path.splitext(name)
        ext = ext if len(ext) <= 16 else ""
        name = stem[: MAX_NAME_CHARS - len(ext)] + ext
    return name


def _create(path: Path) -> bool:
    """Create ``path`` as an empty file; ``False`` when it already exists."""
    try:
        with open(path, "x", encoding="utf-8"):
            return True
    except FileExistsError:
        return False


def reserve_path(directory: Path, name: str) -> Path:
    """Create an empty file named ``name`` in ``directory`` and return it.

    Taken names become ``name (2)`` … ``name (NUMBERED_TRIES)``, then
    ``name-<random hex>``; a handful of probes at most.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stem, ext = os.path.splitext(name)
    numbered = [name] + [f"{stem} ({n}){ext}" for n in range(2, NUMBERED_TRIES + 1)]
    randomized = [f"{stem}-{secrets.token_hex(4)}{ext}" for _ in range(RANDOM_TRIES)]
    for candidate in numbered + randomized:
        if _create(directory / candidate):
            return directory / candidate
    raise FileExistsError(f"no free file name for {name!r} in {directory}")


class Downloads:
    """Saves each download in the background and reports finished ones once.

    Over the session's lifetime at most ``max_count`` downloads are started
    and at most ``max_bytes`` are kept; a download past the count is
    cancelled, one that takes the kept total over ``max_bytes`` is saved in
    full and then deleted, and both are reported as failed.
    """

    def __init__(
        self,
        directory: Path,
        save_timeout: float = SAVE_TIMEOUT_S,
        *,
        max_count: int = MAX_DOWNLOADS,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> None:
        self.directory = directory
        self.save_timeout = save_timeout
        self.max_count = max_count
        self.max_bytes = max_bytes
        self.started = 0
        self._accepted = 0
        self._bytes = 0
        self._tasks: dict[asyncio.Task, str] = {}
        self._cancels: set[asyncio.Task] = set()
        self._finished: list[tuple[str, str]] = []
        self._failed: list[tuple[str, str]] = []

    def start(self, download: DownloadDriver) -> None:
        """Begin saving ``download``; called from the driver's event callback."""
        name = download.suggested_filename or FALLBACK_NAME
        self.started += 1
        if self._accepted >= self.max_count:
            self._failed.append(
                (name, f"refused: the limit of {self.max_count} downloads per session")
            )
            task = asyncio.create_task(_cancel(download))
            self._cancels.add(task)
            task.add_done_callback(self._cancels.discard)
            return
        self._accepted += 1
        self._track(asyncio.create_task(self._save(download, name)), name)

    def _track(self, task: asyncio.Task, name: str) -> None:
        self._tasks[task] = name
        task.add_done_callback(lambda t: self._tasks.pop(t, None))

    async def _save(self, download: DownloadDriver, name: str) -> None:
        path: Path | None = None
        try:
            path = reserve_path(self.directory, safe_filename(name))
            await asyncio.wait_for(download.save_as(str(path)), self.save_timeout)
        except asyncio.TimeoutError:
            await _cancel(download)
            _discard(path)
            self._failed.append((name, f"timed out after {self.save_timeout:g} s"))
            return
        except asyncio.CancelledError:
            _discard(path)
            raise
        except Exception as exc:
            _discard(path)
            logger.debug("Saving a browser download failed", error=type(exc).__name__)
            self._failed.append((name, str(exc) or type(exc).__name__))
            return
        size = _size(path)
        if self._bytes + size > self.max_bytes:
            _discard(path)
            self._failed.append(
                (name, f"refused: over the {self.max_bytes}-byte total download limit")
            )
            return
        self._bytes += size
        self._finished.append((name, str(path)))

    async def wait(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for saves in progress."""
        if self._tasks and timeout > 0:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    def in_progress(self) -> list[str]:
        """Suggested names of downloads still being saved."""
        return list(self._tasks.values())

    def drain(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """``(finished, failed)`` since the last call: ``(name, path)`` and ``(name, reason)``."""
        finished, failed = self._finished, self._failed
        self._finished, self._failed = [], []
        return finished, failed

    def clear(self) -> None:
        """Cancel saves in progress and forget unreported results; the caps keep counting."""
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._finished, self._failed = [], []


async def _cancel(download: DownloadDriver) -> None:
    try:
        await asyncio.wait_for(download.cancel(), CANCEL_TIMEOUT_S)
    except Exception as exc:
        logger.debug("Cancelling a browser download failed", error=type(exc).__name__)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _discard(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
