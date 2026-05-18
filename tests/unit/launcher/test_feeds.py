"""Feed resolution: manifest fetch + release/artifact picking + caching.

Network is monkeypatched at the ``urllib.request.urlopen`` boundary —
no loopback server, no thread. Keeps the test deterministic across
every OS / CI sandbox.
"""

import json

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


# ── Manifest fetch + cache (urlopen monkeypatched) ─────────────────


class _FakeResponse:
    """Stand-in for what ``urlopen()`` yields under ``with``."""

    def __init__(self, body: bytes, headers: dict | None = None, code: int = 200):
        self._body = body
        self._code = code
        # urllib's headers object exposes .get(); a dict suffices.
        self.headers = headers or {
            "ETag": '"abc"',
            "Content-Length": str(len(body)),
        }

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_urlopen(monkeypatch, body: bytes, *, code: int = 200):
    def fake(req, *_, **__):
        return _FakeResponse(body, code=code)

    monkeypatch.setattr(_f.urllib.request, "urlopen", fake)


def _patch_urlopen_raises(monkeypatch, exc: Exception):
    def boom(*_, **__):
        raise exc

    monkeypatch.setattr(_f.urllib.request, "urlopen", boom)


def test_fetch_manifest_writes_cache(monkeypatch, cfg_home):
    _patch_urlopen(monkeypatch, json.dumps(_manifest()).encode("utf-8"))
    s = _s.AppSettings(channel="stable")
    data = _f.fetch_manifest(s)
    assert data["releases"][0]["version"] == "1.5.1"
    assert (cfg_home / "runtime" / "manifest-cache" / "stable.json").is_file()


def test_fetch_manifest_uses_cache_on_network_error(monkeypatch, cfg_home):
    cached_path = cfg_home / "runtime" / "manifest-cache" / "stable.json"
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(json.dumps(_manifest(version="1.0.0")), encoding="utf-8")
    _patch_urlopen_raises(monkeypatch, _f.urllib.error.URLError("dns-bombed"))
    s = _s.AppSettings(channel="stable")
    data = _f.fetch_manifest(s)
    assert data["releases"][0]["version"] == "1.0.0"


def test_resolve_feed_end_to_end(monkeypatch, cfg_home):
    _patch_urlopen(monkeypatch, json.dumps(_manifest()).encode("utf-8"))
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
