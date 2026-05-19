"""Integration journey for the 06b launcher.

One single ``TestLauncherJourney`` class, one fat test method that
drives:

  settings → bundled first_install → run_update (no-op) → manifest
  fetch (urlopen monkeypatched) → run_update with full
  download→extract→smoke→pointer-swap → rollback → reset.

Per ``tests/README.md`` the integration tier holds at most one
workflow function per folder; this file IS that function for the
``launcher`` folder. Do NOT add additional ``def test_*`` here —
fatten this one.

We monkeypatch ``urllib.request.urlopen`` rather than booting a real
loopback HTTPServer. macOS CI runners (and some sandboxed Linux
environments) deadlock inside ``server_bind`` →
``socket.getfqdn("127.0.0.1")`` → ``gethostbyaddr`` because their
DNS resolver chain refuses to answer for the loopback address. The
test gets the same coverage by intercepting the network boundary one
function deeper.
"""

import hashlib
import io
import json
import tarfile

from kohakuterrarium.launcher import downloader as _dl
from kohakuterrarium.launcher import feeds as _feeds
from kohakuterrarium.launcher import paths as _paths
from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher import tree_ops as _tree
from kohakuterrarium.launcher import update_runner as _runner

# ── urlopen fake: routes URLs to in-memory payloads ────────────────


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None, code: int = 200):
        self._body = body
        self._code = code
        self.headers = headers or {
            "ETag": '"x"',
            "Content-Length": str(len(body)),
        }

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._body
            self._body = b""
            return chunk
        chunk = self._body[:size]
        self._body = self._body[size:]
        return chunk

    def getcode(self) -> int:
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Routes:
    """Maps URL paths to response bodies (and a 404 fallback)."""

    def __init__(self):
        self.map: dict[str, bytes] = {}

    def add(self, url: str, body: bytes) -> None:
        self.map[url] = body

    def fetch(self, url: str) -> _FakeResponse:
        for key, body in self.map.items():
            if url == key or url.endswith(key):
                return _FakeResponse(body)
        # 404 — surface as URLError so the production cache-fallback
        # path can be exercised too.
        raise _feeds.urllib.error.HTTPError(url, 404, "not found", {}, None)


# ── Tarball builder ─────────────────────────────────────────────────


def _build_release_tarball(path, *, version: str) -> str:
    members = {
        "manifest.json": json.dumps({"version": version, "build_id": "tb"}).encode(),
        "site-packages/kohakuterrarium/__init__.py": (
            f'__version__ = "{version}"\n'
        ).encode(),
    }
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── The single integration workflow ────────────────────────────────


class TestLauncherJourney:
    def test_full_journey(self, monkeypatch, tmp_path):
        # ── Setup: isolate config dir, point bundled-release at fixture.
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

        # ── 2. Stage the feed: tarball + manifest registered in the
        # _Routes table, urlopen monkeypatched everywhere it gets used.
        new_tar_bytes_path = tmp_path / "release_2.0.0.tar.gz"
        sha = _build_release_tarball(new_tar_bytes_path, version="2.0.0")
        tarball_url = "https://example.test/release/x.tar.gz"
        manifest_url = "https://example.test/stable.json"
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
                            "url": tarball_url,
                            "sha256": sha,
                            "size_bytes": new_tar_bytes_path.stat().st_size,
                        }
                    ],
                }
            ],
        }
        routes = _Routes()
        routes.add(manifest_url, json.dumps(manifest).encode("utf-8"))
        routes.add(tarball_url, new_tar_bytes_path.read_bytes())

        def fake_urlopen(req, *_, **__):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            return routes.fetch(url)

        # Both ``feeds.py`` (manifest fetch) and ``downloader.py``
        # (tarball fetch) reach ``urllib.request.urlopen``; patch the
        # alias on each module so both sites pick up the fake.
        monkeypatch.setattr(_feeds.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(_dl.urllib.request, "urlopen", fake_urlopen)
        # The manifest URL composer normally goes via the GitHub
        # Releases CDN — redirect to our fake host.
        monkeypatch.setattr(_feeds, "_channel_manifest_url", lambda s: manifest_url)

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
