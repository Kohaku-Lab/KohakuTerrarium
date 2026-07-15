"""Download, verify, and safely extract launcher release archives.

Downloads require HTTPS and are atomically promoted only after SHA-256
verification. Extraction rejects path traversal and special entries. Zstandard
support remains optional; gzip and uncompressed tar archives use the standard
library.
"""

import hashlib
import shutil
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from kohakuterrarium.launcher.feeds import USER_AGENT
from kohakuterrarium.launcher.log import get_logger


class DownloadError(RuntimeError):
    """Report a download, integrity, or extraction failure to callers."""


# A zero total indicates that the server omitted Content-Length.
ProgressCallback = Callable[[int, int], None]


def _noop_progress(done: int, total: int) -> None:
    return


def download_to(
    url: str,
    dest: Path,
    expected_sha256: str,
    *,
    progress: ProgressCallback | None = None,
    chunk_size: int = 65536,
    timeout: float = 60.0,
) -> None:
    """Download an HTTPS resource and atomically verify it into ``dest``.

    Data is streamed through SHA-256 into a temporary file. Network, file, or
    checksum failures remove the temporary file and raise :class:`DownloadError`.
    """
    log = get_logger()
    if not url.startswith("https://"):
        raise DownloadError(f"refusing non-https URL {url!r}")
    progress = progress or _noop_progress
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256()
    done = 0
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                try:
                    total = int(cl)
                except ValueError:
                    total = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    try:
                        progress(done, total)
                    except Exception as e:  # pragma: no cover - callback isolation
                        log.debug("progress callback raised: %s", e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if tmp.exists():
            tmp.unlink()
        raise DownloadError(f"download failed: {e}") from e

    actual = h.hexdigest()
    if actual.lower() != expected_sha256.lower():
        tmp.unlink()
        raise DownloadError(
            f"sha256 mismatch for {url}: expected {expected_sha256!r}, got {actual!r}"
        )
    tmp.replace(dest)
    log.info("downloader: wrote %s (%d bytes, sha256 ok)", dest, done)


def _open_tarball(path: Path) -> tarfile.TarFile:
    """Open a supported tar archive, including optional Zstandard streams."""
    name = path.name.lower()
    if name.endswith(".tar.zst") or name.endswith(".tzst"):
        try:
            import zstandard  # noqa: PLC0415 - optional dep
        except ImportError as e:
            raise DownloadError(
                f"{path.name} requires the `zstandard` package "
                "(install it or use a .tar.gz mirror)"
            ) from e
        # Streaming avoids materializing a second decompressed archive on disk.
        dctx = zstandard.ZstdDecompressor()

        src = path.open("rb")
        try:
            reader = dctx.stream_reader(src)
            return tarfile.open(fileobj=reader, mode="r|")
        except Exception:
            src.close()
            raise
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return tarfile.open(str(path), mode="r:gz")
    if name.endswith(".tar"):
        return tarfile.open(str(path), mode="r:")
    raise DownloadError(f"unrecognised tarball extension: {path.name}")


def _safe_member_path(member: tarfile.TarInfo, root: Path) -> Path:
    """Resolve an archive member under ``root`` or reject path traversal."""
    candidate = (root / member.name).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as e:
        raise DownloadError(f"tarball member escapes root: {member.name!r}") from e
    return candidate


def extract_tarball(tarball: Path, dest_dir: Path) -> None:
    """Validate and extract a release archive into ``dest_dir``.

    The validation pass rejects traversal, links, devices, and FIFOs before any
    member is written. Tar parsing and extraction failures are normalized to
    :class:`DownloadError`.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _open_tarball(tarball) as tar:
            for member in tar:
                if member.islnk() or member.issym():
                    raise DownloadError(
                        f"tarball contains link {member.name!r}; refusing"
                    )
                if member.isdev() or member.isfifo():
                    raise DownloadError(
                        f"tarball contains device/fifo {member.name!r}; refusing"
                    )
                _safe_member_path(member, dest_dir)
    except tarfile.TarError as e:
        raise DownloadError(f"tarball validate failed: {e}") from e
    try:
        with _open_tarball(tarball) as tar:
            for member in tar:
                if (
                    member.islnk()
                    or member.issym()
                    or member.isdev()
                    or member.isfifo()
                ):
                    continue  # The validation pass guarantees these are absent.
                _safe_member_path(member, dest_dir)
                # The data filter reinforces the explicit traversal and type checks.
                tar.extract(member, path=str(dest_dir), filter="data")
    except tarfile.TarError as e:
        raise DownloadError(f"tarball extract failed: {e}") from e


def fetch_and_extract(
    url: str,
    expected_sha256: str,
    tarball_cache: Path,
    extract_dir: Path,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    """Download a verified archive and replace its extraction directory.

    The verified archive remains in ``tarball_cache`` for caller-managed retry
    or cleanup. A failed extraction removes the partial destination.
    """
    download_to(url, tarball_cache, expected_sha256, progress=progress)
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    try:
        extract_tarball(tarball_cache, extract_dir)
    except Exception:
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        raise


__all__ = [
    "DownloadError",
    "ProgressCallback",
    "download_to",
    "extract_tarball",
    "fetch_and_extract",
]
