"""HTTPS download + sha256 + tarball extract.

Three primitives:

- :func:`download_to` — streaming HTTPS GET with incremental SHA-256.
- :func:`extract_tarball` — `.tar.zst` (preferred) or `.tar.gz` with
  zip-slip protection.
- :func:`fetch_and_extract` — convenience wrapper used by the runner.

``zstandard`` is an optional dep. When unavailable, only ``.tar.gz``
sources work — the bundled-release artifact and the github_releases
default feed both stay ``.tar.gz``-compatible.
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
    """Network / hash / extract failure surfaced to UI."""


# Progress callback: (bytes_done, bytes_total) -> None. Total is 0 when
# the server didn't send Content-Length.
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
    """Stream-download ``url`` into ``dest``, verifying ``expected_sha256``.

    Atomically: writes to ``dest.tmp`` first, verifies, then renames.
    Removes the tmp file on any failure.
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
                    except Exception as e:  # pragma: no cover - defensive
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


# ── Tarball extract ─────────────────────────────────────────────────


def _open_tarball(path: Path) -> tarfile.TarFile:
    """Open the tarball. ``.tar.zst`` requires the optional ``zstandard``
    dep; ``.tar.gz`` and ``.tar`` use stdlib.
    """
    name = path.name.lower()
    if name.endswith(".tar.zst") or name.endswith(".tzst"):
        try:
            import zstandard  # noqa: PLC0415 - optional dep
        except ImportError as e:
            raise DownloadError(
                f"{path.name} requires the `zstandard` package "
                "(install it or use a .tar.gz mirror)"
            ) from e
        # zstd streaming decompression piped into tarfile.
        dctx = zstandard.ZstdDecompressor()
        # Path can't be Path here — tarfile wants a file-like.
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
    """Zip-slip guard: resolve member path under root, reject escapes."""
    candidate = (root / member.name).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as e:
        raise DownloadError(f"tarball member escapes root: {member.name!r}") from e
    return candidate


def extract_tarball(tarball: Path, dest_dir: Path) -> None:
    """Extract ``tarball`` into ``dest_dir`` (created if missing).

    Zip-slip protected. Symlinks/hardlinks rejected (we don't need them
    in a site-packages tree and they're an attack vector). Devices/FIFOs
    rejected too. Any tarfile-level error (corrupt header, truncated
    body) surfaces as :class:`DownloadError` so callers only need one
    exception class.
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
                    continue  # already rejected in the validation pass
                _safe_member_path(member, dest_dir)
                # ``filter="data"`` (PEP 706 / 3.12+) blocks dangerous
                # tar features — second line of defence after the
                # manual zip-slip guard above.
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
    """Download + extract in one call.

    ``tarball_cache`` is where the tarball lands on disk; we keep it
    after extract so a smoke failure can be retried without re-download.
    Caller cleans up when done.
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
