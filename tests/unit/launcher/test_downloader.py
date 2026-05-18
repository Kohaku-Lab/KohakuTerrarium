"""Downloader: HTTPS-only, sha256 verification, tarball extract + zip-slip.

These are pure unit tests — we monkeypatch ``urllib.request.urlopen``
rather than spinning up a real loopback HTTPServer, so the tests stay
deterministic on every OS (the previous threaded-loopback variant
timed out on some CI runners' sandbox-restricted networking).
"""

import hashlib
import io
import tarfile

import pytest

from kohakuterrarium.launcher import downloader as _d


class _FakeResponse:
    """Minimal stand-in for what ``urlopen()`` returns under ``with``."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self._pos = 0
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_urlopen(monkeypatch, body: bytes):
    """Make ``urllib.request.urlopen`` return ``body`` regardless of URL."""

    def fake_urlopen(req, *_, **__):
        return _FakeResponse(body)

    monkeypatch.setattr(_d.urllib.request, "urlopen", fake_urlopen)


# ── HTTPS guard ─────────────────────────────────────────────────────


def test_download_to_rejects_non_https(tmp_path):
    with pytest.raises(_d.DownloadError):
        _d.download_to("http://example.com/x", tmp_path / "x", "0" * 64)


# ── Streaming + sha verification ────────────────────────────────────


def test_download_to_writes_blob_with_correct_sha(monkeypatch, tmp_path):
    body = b"hello world"
    digest = hashlib.sha256(body).hexdigest()
    _patch_urlopen(monkeypatch, body)
    dest = tmp_path / "out.bin"
    _d.download_to("https://example.test/blob", dest, digest)
    assert dest.read_bytes() == body
    # No tmp left behind.
    assert not dest.with_suffix(dest.suffix + ".tmp").exists()


def test_download_to_aborts_on_sha_mismatch(monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, b"hello world")
    dest = tmp_path / "out.bin"
    with pytest.raises(_d.DownloadError):
        _d.download_to("https://example.test/blob", dest, "0" * 64)
    assert not dest.exists()
    # Mismatch path also cleans up the tmp.
    assert not dest.with_suffix(dest.suffix + ".tmp").exists()


def test_download_to_streams_progress_callback(monkeypatch, tmp_path):
    body = b"x" * 200_000  # >chunk_size to force multiple read() calls
    digest = hashlib.sha256(body).hexdigest()
    _patch_urlopen(monkeypatch, body)
    seen: list[tuple[int, int]] = []
    dest = tmp_path / "out.bin"
    _d.download_to(
        "https://example.test/blob",
        dest,
        digest,
        progress=lambda done, total: seen.append((done, total)),
        chunk_size=65536,
    )
    # Final callback shows full size; we received multiple updates.
    assert seen[-1][0] == len(body)
    assert len(seen) >= 2
    # Progress callback that raises is swallowed without breaking download.
    seen.clear()
    _d.download_to(
        "https://example.test/blob",
        dest,
        digest,
        progress=lambda *_: (_ for _ in ()).throw(RuntimeError("ui crash")),
    )
    assert dest.read_bytes() == body


# ── Tarball extract ─────────────────────────────────────────────────


def _make_targz(path, members: dict[str, bytes]) -> None:
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_extract_tarball_happy_path(tmp_path):
    src = tmp_path / "good.tar.gz"
    _make_targz(
        src,
        {"site-packages/x.py": b"x = 1\n", "scripts/kt": b"#!/usr/bin/env python\n"},
    )
    dest = tmp_path / "out"
    _d.extract_tarball(src, dest)
    assert (dest / "site-packages" / "x.py").read_bytes() == b"x = 1\n"
    assert (dest / "scripts" / "kt").read_bytes() == b"#!/usr/bin/env python\n"


def test_extract_tarball_rejects_zip_slip(tmp_path):
    src = tmp_path / "evil.tar.gz"
    with tarfile.open(str(src), mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../../../etc/passwd")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    dest = tmp_path / "out"
    with pytest.raises(_d.DownloadError):
        _d.extract_tarball(src, dest)


def test_extract_tarball_rejects_symlinks(tmp_path):
    src = tmp_path / "link.tar.gz"
    with tarfile.open(str(src), mode="w:gz") as tar:
        info = tarfile.TarInfo(name="oops")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    dest = tmp_path / "out"
    with pytest.raises(_d.DownloadError):
        _d.extract_tarball(src, dest)


def test_extract_tarball_rejects_unknown_extension(tmp_path):
    bad = tmp_path / "weird.7z"
    bad.write_bytes(b"\x00")
    with pytest.raises(_d.DownloadError):
        _d.extract_tarball(bad, tmp_path / "out")
