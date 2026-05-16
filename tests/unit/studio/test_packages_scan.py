"""Unit tests for :mod:`kohakuterrarium.studio.catalog.packages_scan`."""

from pathlib import Path

import pytest

from kohakuterrarium.studio.catalog import packages_scan as scan_mod


@pytest.fixture(autouse=True)
def _reset_caches():
    scan_mod.invalidate_scan_caches()
    yield
    scan_mod.invalidate_scan_caches()


# ── CatalogEntry / as_registry_dict ─────────────────────────


class TestCatalogEntry:
    def test_creature_dict(self, tmp_path):
        e = scan_mod.CatalogEntry(
            name="alice",
            type="creature",
            path=tmp_path,
            description="d",
            model="m",
            tools=["bash"],
        )
        d = e.as_registry_dict()
        assert d["name"] == "alice"
        assert d["type"] == "creature"
        assert "creatures" not in d

    def test_terrarium_dict_has_creatures(self, tmp_path):
        e = scan_mod.CatalogEntry(
            name="t1",
            type="terrarium",
            path=tmp_path,
            creatures=["alice", "bob"],
        )
        d = e.as_registry_dict()
        assert d["creatures"] == ["alice", "bob"]


# ── _build_package_root_map ─────────────────────────────────


class TestBuildPackageRootMap:
    def test_no_packages_dir(self, monkeypatch):
        # Simulate missing dir.
        monkeypatch.setattr(scan_mod, "PACKAGES_DIR", Path("/definitely/nowhere"))
        assert scan_mod._build_package_root_map() == {}

    def test_with_packages(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "PACKAGES_DIR", tmp_path)
        monkeypatch.setattr(
            scan_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path)}],
        )
        monkeypatch.setattr(scan_mod, "get_package_root", lambda n: tmp_path)
        out = scan_mod._build_package_root_map()
        assert str(tmp_path.resolve()) in out

    def test_get_package_root_none_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "PACKAGES_DIR", tmp_path)
        monkeypatch.setattr(
            scan_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path)}],
        )
        monkeypatch.setattr(scan_mod, "get_package_root", lambda n: None)
        assert scan_mod._build_package_root_map() == {}


# ── to_ref ──────────────────────────────────────────────────


class TestToRef:
    def test_inside_package(self, tmp_path):
        roots = {str(tmp_path.resolve()): "demo"}
        path = tmp_path / "creatures" / "alice"
        out = scan_mod.to_ref(path, roots)
        assert out.startswith("@demo/")
        assert "creatures/alice" in out

    def test_outside_package_returns_str(self, tmp_path):
        roots = {"/some/other/root": "demo"}
        path = tmp_path / "x"
        out = scan_mod.to_ref(path, roots)
        assert out == str(path)


# ── _parse_creature_detail ──────────────────────────────────


class TestParseCreatureDetail:
    def test_no_config_returns_none(self, tmp_path):
        assert scan_mod._parse_creature_detail(tmp_path) is None

    def test_valid_config(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "name: alice\nmodel: m\nsystem_prompt: hello\n"
        )
        # Stub load_agent_config to avoid heavy Agent setup.
        import kohakuterrarium.studio.catalog.packages_scan as m
        from types import SimpleNamespace

        original = m.load_agent_config
        try:
            m.load_agent_config = lambda d: SimpleNamespace(
                name="alice",
                model="m",
                system_prompt="hello",
                tools=[],
            )
            entry = scan_mod._parse_creature_detail(tmp_path)
            assert entry.name == "alice"
            assert entry.type == "creature"
        finally:
            m.load_agent_config = original

    def test_load_agent_config_fails_fallback_yaml(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text(
            "name: alice\nmodel: m\ntools:\n  - {name: bash}\n"
        )

        def boom(d):
            raise RuntimeError("agent build failed")

        monkeypatch.setattr(scan_mod, "load_agent_config", boom)
        entry = scan_mod._parse_creature_detail(tmp_path)
        assert entry.name == "alice"
        assert entry.tools == ["bash"]

    def test_unreadable_yaml_returns_none(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text(":\n: bad yaml")

        def boom(d):
            raise RuntimeError("agent build failed")

        monkeypatch.setattr(scan_mod, "load_agent_config", boom)
        assert scan_mod._parse_creature_detail(tmp_path) is None

    def test_config_yml_fallback(self, tmp_path, monkeypatch):
        (tmp_path / "config.yml").write_text("name: alice")

        from types import SimpleNamespace

        monkeypatch.setattr(
            scan_mod,
            "load_agent_config",
            lambda d: SimpleNamespace(
                name="alice", model="", system_prompt="", tools=[]
            ),
        )
        entry = scan_mod._parse_creature_detail(tmp_path)
        assert entry.name == "alice"


# ── _parse_terrarium_detail ─────────────────────────────────


class TestParseTerrariumDetail:
    def test_no_config_returns_none(self, tmp_path):
        assert scan_mod._parse_terrarium_detail(tmp_path) is None

    def test_valid_config(self, tmp_path):
        (tmp_path / "terrarium.yaml").write_text(
            "name: t1\ncreatures:\n  - {name: alice}\n  - {name: bob}\n"
        )
        entry = scan_mod._parse_terrarium_detail(tmp_path)
        assert entry.name == "t1"
        assert entry.creatures == ["alice", "bob"]

    def test_yml_fallback(self, tmp_path):
        (tmp_path / "terrarium.yml").write_text("name: t1\ncreatures: []")
        entry = scan_mod._parse_terrarium_detail(tmp_path)
        assert entry.name == "t1"

    def test_unreadable_returns_none(self, tmp_path):
        (tmp_path / "terrarium.yaml").write_text(":\n: bad")
        assert scan_mod._parse_terrarium_detail(tmp_path) is None

    def test_terrarium_wrapper_key(self, tmp_path):
        (tmp_path / "terrarium.yaml").write_text(
            "terrarium:\n  name: nested\n  creatures: []\n"
        )
        entry = scan_mod._parse_terrarium_detail(tmp_path)
        assert entry.name == "nested"


# ── _parse_creature_minimal / _parse_terrarium_minimal ──────


class TestParseMinimal:
    def test_creature_minimal_no_config(self, tmp_path):
        out = scan_mod._parse_creature_minimal(tmp_path)
        assert out["name"] == tmp_path.name

    def test_creature_minimal_yml_fallback(self, tmp_path):
        (tmp_path / "config.yml").write_text("name: x\ndescription: d")
        out = scan_mod._parse_creature_minimal(tmp_path)
        assert out["name"] == "x"

    def test_creature_minimal_unreadable_fallback(self, tmp_path):
        (tmp_path / "config.yaml").write_text(":\n: bad")
        out = scan_mod._parse_creature_minimal(tmp_path)
        assert out["name"] == tmp_path.name

    def test_terrarium_minimal_no_config(self, tmp_path):
        out = scan_mod._parse_terrarium_minimal(tmp_path)
        assert out["name"] == tmp_path.name

    def test_terrarium_minimal_yml_fallback(self, tmp_path):
        (tmp_path / "terrarium.yml").write_text("name: x")
        out = scan_mod._parse_terrarium_minimal(tmp_path)
        assert out["name"] == "x"

    def test_terrarium_minimal_unreadable_fallback(self, tmp_path):
        (tmp_path / "terrarium.yaml").write_text(":\n: bad")
        out = scan_mod._parse_terrarium_minimal(tmp_path)
        assert out["name"] == tmp_path.name


# ── scan_catalog ────────────────────────────────────────────


class TestScanCatalog:
    def test_no_packages_no_local(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "list_packages", lambda: [])
        monkeypatch.chdir(tmp_path)
        out = scan_mod.scan_catalog()
        assert out == []

    def test_local_creatures(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "list_packages", lambda: [])
        cdir = tmp_path / "creatures" / "alice"
        cdir.mkdir(parents=True)
        (cdir / "config.yaml").write_text("name: alice\nmodel: m")

        from types import SimpleNamespace

        monkeypatch.setattr(
            scan_mod,
            "load_agent_config",
            lambda d: SimpleNamespace(
                name="alice", model="m", system_prompt="", tools=[]
            ),
        )
        monkeypatch.chdir(tmp_path)
        out = scan_mod.scan_catalog()
        assert any(e.name == "alice" for e in out)
        assert all(e.source for e in out)

    def test_local_terrariums(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "list_packages", lambda: [])
        tdir = tmp_path / "terrariums" / "t1"
        tdir.mkdir(parents=True)
        (tdir / "terrarium.yaml").write_text("name: t1\ncreatures: []")
        monkeypatch.chdir(tmp_path)
        out = scan_mod.scan_catalog()
        assert any(e.name == "t1" for e in out)

    def test_dedup_by_path(self, monkeypatch, tmp_path):
        cdir = tmp_path / "shared"
        cdir.mkdir()
        (cdir / "config.yaml").write_text("name: shared")

        from types import SimpleNamespace

        monkeypatch.setattr(
            scan_mod,
            "load_agent_config",
            lambda d: SimpleNamespace(
                name="shared", model="", system_prompt="", tools=[]
            ),
        )
        monkeypatch.setattr(
            scan_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg",
                    "path": str(tmp_path),
                    "creatures": [{"path": "shared"}],
                    "terrariums": [],
                }
            ],
        )
        monkeypatch.chdir(tmp_path)
        out = scan_mod.scan_catalog()
        names = [e.name for e in out if e.name == "shared"]
        # Path dedup ensures only one entry.
        assert len(names) == 1


# ── scan_creatures_in_dirs / scan_terrariums_in_dirs ────────


class TestScanInDirs:
    def test_empty(self):
        assert scan_mod.scan_creatures_in_dirs([]) == []

    def test_skips_non_dir(self, tmp_path):
        scan_mod.invalidate_scan_caches()
        # base dir doesn't exist.
        assert scan_mod.scan_creatures_in_dirs([tmp_path / "ghost"]) == []

    def test_scans_creatures(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "_build_package_root_map", lambda: {})
        cdir = tmp_path / "alice"
        cdir.mkdir()
        (cdir / "config.yaml").write_text("name: alice")
        out = scan_mod.scan_creatures_in_dirs([tmp_path])
        assert out[0]["name"] == "alice"

    def test_cache_hit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "_build_package_root_map", lambda: {})
        cdir = tmp_path / "alice"
        cdir.mkdir()
        (cdir / "config.yaml").write_text("name: alice")
        first = scan_mod.scan_creatures_in_dirs([tmp_path])
        second = scan_mod.scan_creatures_in_dirs([tmp_path])
        # Same list returned from cache.
        assert first == second

    def test_skips_dir_without_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "_build_package_root_map", lambda: {})
        (tmp_path / "no-config").mkdir()
        out = scan_mod.scan_creatures_in_dirs([tmp_path])
        assert out == []

    def test_skips_non_dir_children(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "_build_package_root_map", lambda: {})
        (tmp_path / "file.txt").write_text("x")
        out = scan_mod.scan_creatures_in_dirs([tmp_path])
        assert out == []

    def test_scans_terrariums(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scan_mod, "_build_package_root_map", lambda: {})
        tdir = tmp_path / "t1"
        tdir.mkdir()
        (tdir / "terrarium.yaml").write_text("name: t1")
        out = scan_mod.scan_terrariums_in_dirs([tmp_path])
        assert out[0]["name"] == "t1"


# ── dedupe_dirs ─────────────────────────────────────────────


class TestDedupeDirs:
    def test_empty(self):
        assert scan_mod.dedupe_dirs([]) == []

    def test_resolves_and_dedups(self, tmp_path):
        out = scan_mod.dedupe_dirs([str(tmp_path), str(tmp_path)])
        assert len(out) == 1


# ── invalidate_scan_caches ──────────────────────────────────


class TestInvalidateScanCaches:
    def test_invalidate(self):
        # Just exercise the function — globals reset.
        scan_mod._creatures_cache = ([], 0, ())
        scan_mod._terrariums_cache = ([], 0, ())
        scan_mod.invalidate_scan_caches()
        assert scan_mod._creatures_cache is None
        assert scan_mod._terrariums_cache is None
