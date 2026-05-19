"""tree_ops: pointer file, partial promote, list+GC, rollback."""

import json
import os

import pytest

from kohakuterrarium.launcher import paths as _p
from kohakuterrarium.launcher import tree_ops as _t


@pytest.fixture
def cfg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _make_version(version: str, *, build_id: str = "") -> None:
    """Lay down a stub version tree with a manifest.json."""
    root = _p.version_dir(version)
    (root / "site-packages").mkdir(parents=True, exist_ok=True)
    info = {"version": version, "build_id": build_id, "generated_at": ""}
    (root / "manifest.json").write_text(json.dumps(info), encoding="utf-8")


# ── pointer ─────────────────────────────────────────────────────────


class TestPointer:
    def test_round_trip(self, cfg_home):
        _t.write_active_pointer("1.5.1", build_id="b1")
        ptr = _t.read_active_pointer()
        assert ptr is not None
        assert ptr.version == "1.5.1"
        assert ptr.build_id == "b1"
        assert ptr.installed_at  # populated

    def test_missing_returns_none(self, cfg_home):
        assert _t.read_active_pointer() is None

    def test_corrupt_returns_none(self, cfg_home):
        p = _p.active_pointer_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert _t.read_active_pointer() is None

    def test_clear(self, cfg_home):
        _t.write_active_pointer("1.5.1")
        _t.clear_active_pointer()
        assert _t.read_active_pointer() is None


# ── partial promote / sweep ─────────────────────────────────────────


class TestPartials:
    def test_promote_rename(self, cfg_home):
        partial = _t.partial_dir_for("1.5.1")
        partial.mkdir(parents=True)
        (partial / "manifest.json").write_text(
            json.dumps({"version": "1.5.1"}), encoding="utf-8"
        )
        final = _t.promote_partial("1.5.1")
        assert final == _p.version_dir("1.5.1")
        assert final.is_dir()
        assert not partial.exists()

    def test_promote_missing_raises(self, cfg_home):
        with pytest.raises(_t.TreeOpError):
            _t.promote_partial("nope")

    def test_remove_partial_idempotent(self, cfg_home):
        partial = _t.partial_dir_for("1.5.1")
        partial.mkdir(parents=True)
        _t.remove_partial("1.5.1")
        _t.remove_partial("1.5.1")  # no error on second call
        assert not partial.exists()

    def test_sweep_stale_partials(self, cfg_home):
        (_p.versions_dir() / "x.partial").mkdir(parents=True)
        (_p.versions_dir() / "y.partial").mkdir(parents=True)
        (_p.versions_dir() / "1.5.0").mkdir(parents=True)
        removed = _t.sweep_stale_partials()
        assert set(removed) == {"x.partial", "y.partial"}
        # Live version dir untouched.
        assert (_p.versions_dir() / "1.5.0").is_dir()


# ── listing + GC ────────────────────────────────────────────────────


class TestListingAndGc:
    def test_list_installed_newest_first(self, cfg_home):
        _make_version("1.4.0")
        _make_version("1.5.0")
        _make_version("1.5.1")
        # Touch mtimes so ordering is deterministic when manifest's
        # generated_at is empty (test's manifest uses "").
        for v, ts_off in (("1.4.0", 0), ("1.5.0", 100), ("1.5.1", 200)):
            os.utime(_p.version_dir(v), (1000000 + ts_off, 1000000 + ts_off))
        installed = _t.list_installed_versions()
        versions = [p.version for p in installed]
        assert versions == ["1.5.1", "1.5.0", "1.4.0"]

    def test_gc_keeps_active_plus_n(self, cfg_home):
        for v in ("1.4.0", "1.5.0", "1.5.1", "1.5.2"):
            _make_version(v)
        # mtime ordering: 1.5.2 newest
        for v, ts in (("1.4.0", 1), ("1.5.0", 2), ("1.5.1", 3), ("1.5.2", 4)):
            os.utime(_p.version_dir(v), (1000000 + ts, 1000000 + ts))
        # always_keep={1.4.0} (active) + keep=2 newest → kept = {1.4.0, 1.5.2, 1.5.1}
        removed = _t.gc_old_versions(keep=2, always_keep={"1.4.0"})
        assert set(removed) == {"1.5.0"}
        # 1.4.0 retained even though it's oldest.
        assert _p.version_dir("1.4.0").is_dir()
        assert _p.version_dir("1.5.2").is_dir()
        assert _p.version_dir("1.5.1").is_dir()
        assert not _p.version_dir("1.5.0").exists()


# ── rollback ────────────────────────────────────────────────────────


class TestRollback:
    def test_revert_to_previous(self, cfg_home):
        _make_version("1.5.0")
        _make_version("1.5.1")
        for v, ts in (("1.5.0", 1), ("1.5.1", 2)):
            os.utime(_p.version_dir(v), (1000000 + ts, 1000000 + ts))
        _t.write_active_pointer("1.5.1", build_id="b2")
        prev = _t.revert_active_pointer()
        assert prev.version == "1.5.0"
        # Pointer now reads 1.5.0.
        ptr = _t.read_active_pointer()
        assert ptr is not None and ptr.version == "1.5.0"

    def test_revert_with_nothing_to_roll_back_raises(self, cfg_home):
        _make_version("1.5.0")
        _t.write_active_pointer("1.5.0")
        with pytest.raises(_t.TreeOpError):
            _t.revert_active_pointer()


# ── smoke ───────────────────────────────────────────────────────────


class TestSmoke:
    """End-to-end exercise of ``smoke_test_tree`` against a real subprocess.

    Crafts a minimal ``site-packages/kohakuterrarium/__init__.py`` and
    confirms the probe imports it under the current ``sys.executable``.
    """

    def test_smoke_returns_version_from_init(self, tmp_path):
        root = tmp_path / "v1"
        site = root / "site-packages" / "kohakuterrarium"
        site.mkdir(parents=True)
        (site / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        version = _t.smoke_test_tree(root)
        assert version == "1.2.3"

    def test_smoke_missing_site_packages_raises(self, tmp_path):
        root = tmp_path / "vBAD"
        root.mkdir()
        with pytest.raises(_t.TreeOpError):
            _t.smoke_test_tree(root)

    def test_smoke_missing_kohakuterrarium_pkg_raises(self, tmp_path):
        root = tmp_path / "vIncomplete"
        # Has site-packages/ but no kohakuterrarium/ inside it — an
        # incomplete tarball.
        (root / "site-packages").mkdir(parents=True)
        with pytest.raises(_t.TreeOpError):
            _t.smoke_test_tree(root)

    def test_smoke_missing_version_string_returns_placeholder(self, tmp_path):
        root = tmp_path / "vNoVer"
        site = root / "site-packages" / "kohakuterrarium"
        site.mkdir(parents=True)
        (site / "__init__.py").write_text("# no version assigned\n", encoding="utf-8")
        assert _t.smoke_test_tree(root) == "<no-version>"
