"""Feed resolution: manifest fetch + release/artifact picking + caching."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kohakuterrarium.launcher import feeds as _f
from kohakuterrarium.launcher import settings as _s

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def cfg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _manifest(version="1.5.1", plat="linux-x64", abi="cp313") -> dict:
    return {
        "schema": 1,
        "channel": "stable",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "releases": [
            {
                "version": version,
                "build_id": "b1",
                "release_notes_url": "https://example.test/notes",
                "artifacts": [
                    {
                        "platform": plat,
                        "py_abi": abi,
                        "url": f"https://example.test/{version}/x.tar.zst",
                        "sha256": "f" * 64,
                        "size_bytes": 12345,
                    }
                ],
            }
        ],
    }


# ── _pick_release / _pick_artifact ──────────────────────────────────


class TestPickers:
    def test_pick_release_returns_first_when_no_pin(self):
        m = _manifest()
        # Add a second, older release.
        m["releases"].append(
            {
                "version": "1.5.0",
                "build_id": "b0",
                "artifacts": m["releases"][0]["artifacts"],
            }
        )
        rel = _f._pick_release(m, pinned=None)
        assert rel["version"] == "1.5.1"

    def test_pick_release_honours_pin(self):
        m = _manifest()
        m["releases"].append(
            {
                "version": "1.5.0",
                "build_id": "b0",
                "artifacts": m["releases"][0]["artifacts"],
            }
        )
        rel = _f._pick_release(m, pinned="1.5.0")
        assert rel["version"] == "1.5.0"

    def test_pick_release_missing_pin_raises(self):
        with pytest.raises(_f.FeedError):
            _f._pick_release(_manifest(), pinned="9.9.9")

    def test_pick_artifact_matches_plat_and_abi(self):
        m = _manifest(plat="linux-x64", abi="cp313")
        art = _f._pick_artifact(m["releases"][0], "linux-x64", "cp313")
        assert art["sha256"] == "f" * 64

    def test_pick_artifact_no_match_raises(self):
        m = _manifest(plat="linux-x64", abi="cp313")
        with pytest.raises(_f.FeedError):
            _f._pick_artifact(m["releases"][0], "win-x64", "cp313")


# ── Manifest URL composition ────────────────────────────────────────


class TestManifestUrl:
    def test_github_releases_url_pattern(self):
        s = _s.AppSettings(
            feed=_s.FeedConfig(kind="github_releases", repo="a/b"),
            channel="stable",
        )
        url = _f._channel_manifest_url(s)
        assert (
            url
            == "https://github.com/a/b/releases/download/manifests-stable/stable.json"
        )

    def test_custom_url_pattern(self):
        s = _s.AppSettings(
            feed=_s.FeedConfig(kind="custom", url="https://m.example/kt"),
            channel="nightly",
        )
        url = _f._channel_manifest_url(s)
        assert url == "https://m.example/kt/nightly.json"


# ── Live HTTP fetch + cache ────────────────────────────────────────


class _ManifestHandler(BaseHTTPRequestHandler):
    payload: bytes = b""
    requests: int = 0

    def do_GET(self):  # noqa: N802 - http handler convention
        _ManifestHandler.requests += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", '"abc"')
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_):
        pass


@pytest.fixture
def fake_manifest_server(cfg_home):
    _ManifestHandler.requests = 0
    _ManifestHandler.payload = json.dumps(_manifest()).encode("utf-8")
    srv = HTTPServer(("127.0.0.1", 0), _ManifestHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()


def test_fetch_manifest_writes_cache(monkeypatch, fake_manifest_server, cfg_home):
    # _channel_manifest_url enforces https — patch it directly to point
    # at our loopback server.
    monkeypatch.setattr(
        _f, "_channel_manifest_url", lambda s: fake_manifest_server + "/c.json"
    )
    s = _s.AppSettings(channel="stable")
    data = _f.fetch_manifest(s)
    assert data["releases"][0]["version"] == "1.5.1"
    assert (cfg_home / "runtime" / "manifest-cache" / "stable.json").is_file()


def test_fetch_manifest_uses_cache_on_network_error(monkeypatch, cfg_home):
    cached_path = cfg_home / "runtime" / "manifest-cache" / "stable.json"
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(json.dumps(_manifest(version="1.0.0")), encoding="utf-8")
    monkeypatch.setattr(
        _f, "_channel_manifest_url", lambda s: "https://nowhere.invalid/x.json"
    )
    s = _s.AppSettings(channel="stable")
    data = _f.fetch_manifest(s)
    assert data["releases"][0]["version"] == "1.0.0"


def test_resolve_feed_end_to_end(monkeypatch, fake_manifest_server, cfg_home):
    monkeypatch.setattr(
        _f, "_channel_manifest_url", lambda s: fake_manifest_server + "/c.json"
    )
    s = _s.AppSettings(channel="stable")
    target = _f.resolve_feed(s, platform_tag="linux-x64", py_abi_tag="cp313")
    assert target.version == "1.5.1"
    assert target.sha256 == "f" * 64
    assert target.url == "https://example.test/1.5.1/x.tar.zst"


# ── Platform / ABI tags are stable ──────────────────────────────────


def test_current_platform_tag_is_known():
    assert (
        _f.current_platform_tag()
        in (
            "linux-x64",
            "linux-arm64",
            "macos-x64",
            "macos-arm64",
            "win-x64",
        )
        or "-" in _f.current_platform_tag()
    )


def test_current_py_abi_tag_pattern():
    t = _f.current_py_abi_tag()
    assert t.startswith(("cp", "pp")) or any(ch.isdigit() for ch in t)


def test_list_available_releases_filters_to_match():
    m = _manifest(plat="linux-x64", abi="cp313")
    out = _f.list_available_releases(m, platform_tag="linux-x64", py_abi_tag="cp313")
    assert len(out) == 1
    assert out[0]["version"] == "1.5.1"

    miss = _f.list_available_releases(m, platform_tag="win-x64", py_abi_tag="cp313")
    assert miss == []
