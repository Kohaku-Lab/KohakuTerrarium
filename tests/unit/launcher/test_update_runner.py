"""update_runner: orchestration around feed → download → smoke → swap."""

import io
import json
import tarfile

import pytest

from kohakuterrarium.launcher import paths as _p
from kohakuterrarium.launcher import settings as _s
from kohakuterrarium.launcher import tree_ops as _t
from kohakuterrarium.launcher import update_runner as _r


@pytest.fixture
def cfg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _make_smoke_passing_tarball(path, *, version: str) -> None:
    """Build a structurally-valid release tarball.

    Tests stub ``smoke_test_tree`` so the contents don't need to be
    runnable — we only need ``site-packages/`` + ``manifest.json``
    inside a single top-level directory so the launcher's extract +
    promote paths exercise the real shapes.
    """
    site = {
        "manifest.json": json.dumps({"version": version, "build_id": "tb"}).encode(),
        "site-packages/kohakuterrarium/__init__.py": f'__version__ = "{version}"\n'.encode(),
    }
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, data in site.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


# ── _install_from_bundled ───────────────────────────────────────────


def test_first_install_uses_bundled_when_present(monkeypatch, cfg_home):
    bundled = cfg_home / "bundled-release"
    bundled.mkdir()
    tb = bundled / "kohakuterrarium-9.9.9-linux-x64-py3.13.tar.gz"
    _make_smoke_passing_tarball(tb, version="9.9.9")
    # Point the bundled-release probe at our tmp path.
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [bundled])
    # Stub smoke so we don't actually need a working python install.
    monkeypatch.setattr(_r, "smoke_test_tree", lambda d: "9.9.9")

    result = _r.first_install()
    assert result.ok
    assert result.version == "9.9.9"
    # Pointer + version dir created.
    ptr = _t.read_active_pointer()
    assert ptr is not None and ptr.version == "9.9.9"
    assert _p.version_dir("9.9.9").is_dir()


def test_first_install_falls_through_when_bundled_corrupt(monkeypatch, cfg_home):
    bundled = cfg_home / "bundled-release"
    bundled.mkdir()
    # Empty file with the right name to trip the probe but fail extract.
    (bundled / "kohakuterrarium-bad-linux-x64-py3.13.tar.gz").write_bytes(b"")
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [bundled])
    # Make feed resolution fail too, so we surface the bundled error.
    monkeypatch.setattr(
        _r,
        "_install_from_feed",
        lambda cfg, prog, *, is_update: _r.UpdateResult(ok=False, error="offline"),
    )
    result = _r.first_install()
    assert not result.ok


# ── _install_from_feed via run_update ──────────────────────────────


def test_run_update_skips_when_same_version(monkeypatch, cfg_home):
    # Pre-seed active pointer.
    _t.write_active_pointer("1.5.1", build_id="b")
    target = _r.ReleaseTarget = type(
        "T",
        (),
        {
            "version": "1.5.1",
            "build_id": "b",
            "url": "https://x",
            "sha256": "0" * 64,
            "size_bytes": 0,
            "platform": "linux-x64",
            "py_abi": "cp313",
            "release_notes_url": None,
        },
    )()
    monkeypatch.setattr(_r, "resolve_feed", lambda cfg, **k: target)
    result = _r.run_update()
    assert result.ok
    assert result.skipped_reason == "up-to-date"


def test_run_update_happy_path(monkeypatch, cfg_home):
    """Full happy path: feed → download → extract → smoke → swap pointer."""
    new_tar = cfg_home / "incoming.tar.gz"
    _make_smoke_passing_tarball(new_tar, version="2.0.0")

    class _Target:
        version = "2.0.0"
        build_id = "tb"
        url = "https://example.test/x.tar.gz"
        sha256 = "0" * 64
        size_bytes = 0
        platform = "linux-x64"
        py_abi = "cp313"
        release_notes_url = None

    monkeypatch.setattr(_r, "resolve_feed", lambda cfg, **k: _Target())

    def fake_fetch_and_extract(url, sha, tarball_cache, extract_dir, progress=None):
        # Pretend we downloaded the tarball + extracted it into extract_dir.
        # We simulate by extracting our prebuilt fixture tar into extract_dir.
        import tarfile as _tf
        import shutil as _sh

        if extract_dir.exists():
            _sh.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        with _tf.open(str(new_tar), mode="r:gz") as t:
            t.extractall(str(extract_dir))

    monkeypatch.setattr(_r, "fetch_and_extract", fake_fetch_and_extract)
    monkeypatch.setattr(_r, "smoke_test_tree", lambda d: "2.0.0")

    result = _r.run_update()
    assert result.ok, result.error
    assert result.version == "2.0.0"
    assert result.restart_required
    ptr = _t.read_active_pointer()
    assert ptr is not None and ptr.version == "2.0.0"


def test_run_update_aborts_on_smoke_failure(monkeypatch, cfg_home):
    new_tar = cfg_home / "incoming.tar.gz"
    _make_smoke_passing_tarball(new_tar, version="2.0.0")

    class _Target:
        version = "2.0.0"
        build_id = ""
        url = "https://example.test/x.tar.gz"
        sha256 = "0" * 64
        size_bytes = 0
        platform = "linux-x64"
        py_abi = "cp313"
        release_notes_url = None

    monkeypatch.setattr(_r, "resolve_feed", lambda cfg, **k: _Target())

    def fake_fetch_and_extract(url, sha, tarball_cache, extract_dir, progress=None):
        import tarfile as _tf
        import shutil as _sh

        if extract_dir.exists():
            _sh.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        with _tf.open(str(new_tar), mode="r:gz") as t:
            t.extractall(str(extract_dir))

    monkeypatch.setattr(_r, "fetch_and_extract", fake_fetch_and_extract)

    def boom(_d):
        raise _t.TreeOpError("kt --version returned 1")

    monkeypatch.setattr(_r, "smoke_test_tree", boom)

    result = _r.run_update()
    assert not result.ok
    assert "smoke" in (result.error or "")
    # Partial cleaned up.
    assert not _t.partial_dir_for("2.0.0").exists()


# ── rollback ────────────────────────────────────────────────────────


def test_rollback_reverts_pointer(monkeypatch, cfg_home):
    # Lay down two installed versions.
    for v in ("1.0.0", "1.1.0"):
        root = _p.version_dir(v)
        (root / "site-packages").mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps({"version": v}), encoding="utf-8"
        )
    import os as _os

    _os.utime(_p.version_dir("1.0.0"), (1000, 1000))
    _os.utime(_p.version_dir("1.1.0"), (2000, 2000))
    _t.write_active_pointer("1.1.0")
    result = _r.rollback()
    assert result.ok
    assert result.version == "1.0.0"


# ── maybe_update mode dispatch ──────────────────────────────────────


class TestMaybeUpdateModes:
    def test_manual(self, monkeypatch, cfg_home):
        s = _s.AppSettings(update=_s.UpdateConfig(mode="manual"))
        _s.save(s)
        result = _r.maybe_update()
        assert result.skipped_reason == "manual"

    def test_notify_only(self, monkeypatch, cfg_home):
        s = _s.AppSettings(update=_s.UpdateConfig(mode="notify-on-launch"))
        _s.save(s)
        result = _r.maybe_update()
        assert result.skipped_reason == "notify-only"
