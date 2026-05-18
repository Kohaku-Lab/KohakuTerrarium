"""Integration journey for the 06b launcher.

One single ``TestLauncherJourney`` class, one fat test method that
drives:

  settings → bundled first_install → run_update (no-op) → manifest
  fetch via loopback HTTP → run_update (real download path, with
  smoke stubbed) → rollback → reset → state verified at every step.

Per ``tests/README.md`` the integration tier holds at most one
workflow function per folder; this file IS that function for the
``launcher`` folder. Do not add additional ``def test_*`` here —
fatten this one.
"""

import hashlib
import io
import json
import os
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kohakuterrarium.launcher import feeds as _feeds
from kohakuterrarium.launcher import paths as _paths
from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher import tree_ops as _tree
from kohakuterrarium.launcher import update_runner as _runner

# ── Fixture HTTP server serving manifest + tarball ─────────────────


class _LauncherFakeFeed(BaseHTTPRequestHandler):
    manifest_payload: bytes = b""
    tarball_payload: bytes = b""
    tarball_url_path: str = "/release/x.tar.gz"
    manifest_url_path: str = "/stable.json"

    def do_GET(self):  # noqa: N802
        if self.path.endswith(self.manifest_url_path):
            self._serve(self.manifest_payload, "application/json")
            return
        if self.path.endswith(self.tarball_url_path):
            self._serve(self.tarball_payload, "application/octet-stream")
            return
        self.send_response(404)
        self.end_headers()

    def _serve(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def fake_feed_server():
    srv = HTTPServer(("127.0.0.1", 0), _LauncherFakeFeed)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()


# ── Tarball builder ─────────────────────────────────────────────────


def _build_release_tarball(path, *, version: str) -> str:
    members = {
        "manifest.json": json.dumps({"version": version, "build_id": "tb"}).encode(),
        "site-packages/kohakuterrarium/__init__.py": (
            f'__version__ = "{version}"\n'
        ).encode(),
        "scripts/kt": b"#!/bin/sh\necho ok\n",
    }
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── The single integration workflow ────────────────────────────────


class TestLauncherJourney:
    def test_full_journey(self, monkeypatch, tmp_path, fake_feed_server):
        # ── Setup: isolate config dir, point bundled-release at fixture
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        bundled = tmp_path / "bundled-release"
        bundled.mkdir()
        bundled_tar = bundled / "kohakuterrarium-1.0.0-linux-x64-py3.13.tar.gz"
        _build_release_tarball(bundled_tar, version="1.0.0")
        monkeypatch.setattr(
            _paths, "_candidate_bundled_release_dirs", lambda: [bundled]
        )
        # Stub smoke so we don't need a working python on the version tree.
        monkeypatch.setattr(_runner, "smoke_test_tree", lambda d: "stub-ok")

        # ── 1. First install consumes the bundled tarball.
        result = _runner.first_install()
        assert result.ok, result.error
        assert result.version == "1.0.0"
        ptr = _tree.read_active_pointer()
        assert ptr is not None and ptr.version == "1.0.0"

        cfg = _settings.load()
        assert cfg.runtime.active_version == "1.0.0"

        # ── 2. Stand up the fake feed serving manifest + new tarball.
        _, base_url = fake_feed_server
        new_tar_bytes_path = tmp_path / "release_2.0.0.tar.gz"
        sha = _build_release_tarball(new_tar_bytes_path, version="2.0.0")
        _LauncherFakeFeed.tarball_payload = new_tar_bytes_path.read_bytes()
        _LauncherFakeFeed.tarball_url_path = "/release/x.tar.gz"
        manifest = {
            "schema": 1,
            "channel": "stable",
            "generated_at": "2026-05-19T00:00:00+00:00",
            "releases": [
                {
                    "version": "2.0.0",
                    "build_id": "newer",
                    "release_notes_url": None,
                    "artifacts": [
                        {
                            "platform": _feeds.current_platform_tag(),
                            "py_abi": _feeds.current_py_abi_tag(),
                            "url": f"{base_url}/release/x.tar.gz",
                            "sha256": sha,
                            "size_bytes": new_tar_bytes_path.stat().st_size,
                        }
                    ],
                }
            ],
        }
        _LauncherFakeFeed.manifest_payload = json.dumps(manifest).encode()
        monkeypatch.setattr(
            _feeds,
            "_channel_manifest_url",
            lambda s: f"{base_url}/stable.json",
        )
        # Pre-emptively allow non-https for the test downloader call by
        # patching the strict guard.
        from kohakuterrarium.launcher import downloader as _dl

        real_download = _dl.download_to

        def loose_download(url, dest, sha, **kw):
            # Bypass https-only guard by injecting the bytes directly
            # so the integration journey can run on loopback http.
            import urllib.request

            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            actual = hashlib.sha256(body).hexdigest()
            if actual.lower() != sha.lower():
                raise _dl.DownloadError("sha mismatch")
            dest.write_bytes(body)
            _ = kw

        monkeypatch.setattr(_dl, "download_to", loose_download)
        # Also patch the alias the runner imported.
        monkeypatch.setattr(_runner, "fetch_and_extract", _dl.fetch_and_extract)

        # ── 3. Run an update; should pick up 2.0.0 from the feed.
        result = _runner.run_update()
        assert result.ok, result.error
        assert result.version == "2.0.0"
        assert _tree.read_active_pointer().version == "2.0.0"
        assert _paths.version_dir("1.0.0").is_dir()  # prior preserved
        assert _paths.version_dir("2.0.0").is_dir()

        # ── 4. Second update is a no-op (still 2.0.0 in the manifest).
        result = _runner.run_update()
        assert result.ok
        assert result.skipped_reason == "up-to-date"

        # ── 5. Rollback flips pointer back to 1.0.0.
        result = _runner.rollback()
        assert result.ok
        assert result.version == "1.0.0"
        assert _tree.read_active_pointer().version == "1.0.0"

        # ── 6. Reset wipes versions and re-runs first_install (bundled).
        result = _runner.reset()
        assert result.ok
        assert result.version == "1.0.0"
        # After reset, only the bundled version exists on disk.
        installed = {p.version for p in _tree.list_installed_versions()}
        assert installed == {"1.0.0"}

        # ── 7. Settings round-trip survived everything.
        cfg = _settings.load()
        assert cfg.runtime.active_version == "1.0.0"
        _ = real_download
        _ = os  # silence
