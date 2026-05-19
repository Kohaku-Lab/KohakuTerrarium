"""Path-resolver behaviour for the launcher (06b layout).

Covers ``versions_dir``, ``version_dir``, ``active_pointer_path``,
``manifest_cache_dir``, ``legacy_venv_dir``, and the
``bundled_release_dir`` probe.
"""

from pathlib import Path

from kohakuterrarium.launcher import paths as _p


def test_config_home_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    assert _p.config_home() == tmp_path


def test_runtime_layout_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    assert _p.runtime_dir() == tmp_path / "runtime"
    assert _p.versions_dir() == tmp_path / "runtime" / "versions"
    assert _p.version_dir("1.5.1") == tmp_path / "runtime" / "versions" / "1.5.1"
    assert _p.active_pointer_path() == tmp_path / "runtime" / "active"
    assert _p.manifest_cache_dir() == tmp_path / "runtime" / "manifest-cache"
    assert _p.settings_path() == tmp_path / "app-settings.json"
    assert _p.lock_path() == tmp_path / "runtime" / ".update.lock"
    assert _p.legacy_venv_dir() == tmp_path / "runtime" / "venv"


def _place_release_tarball(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    (parent / "kohakuterrarium-1.5.0-linux-x64-py3.13.tar.zst").write_bytes(b"")
    return parent


class TestHasReleaseTarball:
    def test_true_when_kohakuterrarium_tarball_present(self, tmp_path):
        bundle = _place_release_tarball(tmp_path / "bundled-release")
        assert _p._has_release_tarball(bundle) is True

    def test_false_when_directory_missing(self, tmp_path):
        assert _p._has_release_tarball(tmp_path / "nope") is False

    def test_false_when_only_other_files_present(self, tmp_path):
        bundle = tmp_path / "bundled-release"
        bundle.mkdir()
        (bundle / "README.md").write_text("hi", encoding="utf-8")
        assert _p._has_release_tarball(bundle) is False

    def test_false_when_path_is_a_file(self, tmp_path):
        (tmp_path / "bundled-release").write_bytes(b"")
        assert _p._has_release_tarball(tmp_path / "bundled-release") is False


class TestBundledReleaseDir:
    def test_returns_none_when_no_candidate_has_tarball(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            _p,
            "_candidate_bundled_release_dirs",
            lambda: [tmp_path / "a", tmp_path / "b"],
        )
        assert _p.bundled_release_dir() is None

    def test_returns_first_matching_candidate(self, monkeypatch, tmp_path):
        c1 = tmp_path / "c1"
        c2 = tmp_path / "c2"
        _place_release_tarball(c2)
        monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [c1, c2])
        assert _p.bundled_release_dir() == c2

    def test_prefers_earlier_candidate(self, monkeypatch, tmp_path):
        c1 = tmp_path / "c1"
        c2 = tmp_path / "c2"
        for c in (c1, c2):
            _place_release_tarball(c)
        monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [c1, c2])
        assert _p.bundled_release_dir() == c1


class TestVersionEntrypointHelpers:
    def test_site_packages_dir(self, tmp_path):
        assert _p.site_packages_dir(tmp_path) == tmp_path / "site-packages"

    def test_manifest_path(self, tmp_path):
        assert _p.manifest_path(tmp_path) == tmp_path / "manifest.json"

    def test_python_for_is_sys_executable(self, tmp_path):
        # The launcher always uses sys.executable; version_root is
        # purely cosmetic in the signature.
        assert str(_p.python_for(tmp_path)) == _p.sys.executable
