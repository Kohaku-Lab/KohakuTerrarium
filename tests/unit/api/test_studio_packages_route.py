"""Unit tests for :mod:`kohakuterrarium.api.studio.routes.packages`."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.studio.routes import packages as packages_mod

PREFIX = "/x"


def _client(monkeypatch=None, package_root=None) -> TestClient:
    app = FastAPI()
    app.include_router(packages_mod.router, prefix=PREFIX)
    return TestClient(app)


class TestPackagesListRoute:
    def test_list_all(self, monkeypatch):
        monkeypatch.setattr(
            packages_mod, "list_installed_packages", lambda: [{"name": "demo"}]
        )
        r = _client().get(PREFIX)
        assert r.status_code == 200
        assert r.json() == [{"name": "demo"}]


class TestPackageDetail:
    def test_summary_not_found(self, monkeypatch):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: None)
        r = _client().get(PREFIX + "/ghost")
        assert r.status_code == 404

    def test_summary_basic(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(
            packages_mod,
            "_load_manifest",
            lambda root: {
                "name": "pkg",
                "version": "1.0",
                "tools": [{"name": "t1"}, {"name": "t2"}],
                "creatures": [{"name": "c"}],
            },
        )
        r = _client().get(PREFIX + "/pkg")
        body = r.json()
        assert body["name"] == "pkg"
        assert body["tools"] == 2
        assert body["creatures"] == 1
        assert body["triggers"] == 0

    def test_has_python_deps_from_requirements_txt(self, monkeypatch, tmp_path):
        (tmp_path / "requirements.txt").write_text("foo==1.0\n")
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(packages_mod, "_load_manifest", lambda r: {})
        body = _client().get(PREFIX + "/pkg").json()
        assert body["has_python_dependencies"] is True


class TestListPackageCreatures:
    def test_no_creatures_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        r = _client().get(PREFIX + "/pkg/creatures")
        assert r.status_code == 200
        assert r.json() == []

    def test_with_creatures(self, monkeypatch, tmp_path):
        cd = tmp_path / "creatures"
        cd.mkdir()
        c = cd / "alice"
        c.mkdir()
        (c / "config.yaml").write_text("name: alice")
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        body = _client().get(PREFIX + "/pkg/creatures").json()
        assert any(e["name"] == "alice" for e in body)

    def test_skips_creatures_without_config(self, monkeypatch, tmp_path):
        cd = tmp_path / "creatures"
        cd.mkdir()
        (cd / "lonely").mkdir()
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        body = _client().get(PREFIX + "/pkg/creatures").json()
        assert body == []

    def test_skips_stray_files_in_creatures_dir(self, monkeypatch, tmp_path):
        # A loose file (not a directory) sitting in ``creatures/`` is
        # skipped — only real creature subdirs with a config surface.
        cd = tmp_path / "creatures"
        cd.mkdir()
        (cd / "README.md").write_text("not a creature")
        good = cd / "bob"
        good.mkdir()
        (good / "config.yaml").write_text("name: bob")
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        body = _client().get(PREFIX + "/pkg/creatures").json()
        names = {e["name"] for e in body}
        assert names == {"bob"}


class TestListPackageModules:
    def test_no_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        r = _client().get(PREFIX + "/pkg/modules/tools")
        assert r.status_code == 200
        assert r.json() == []

    def test_with_python_modules(self, monkeypatch, tmp_path):
        md = tmp_path / "modules" / "tools"
        md.mkdir(parents=True)
        (md / "a.py").write_text("# a")
        (md / "skip.txt").write_text("x")
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        body = _client().get(PREFIX + "/pkg/modules/tools").json()
        names = {e["name"] for e in body}
        assert "a" in names
        assert "skip" not in names


class TestExtensionEndpoints:
    @pytest.mark.parametrize("kind", ["plugins", "tools", "triggers", "io", "skills"])
    def test_extension_404_when_pkg_missing(self, monkeypatch, kind):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: None)
        r = _client().get(PREFIX + f"/ghost/{kind}")
        assert r.status_code == 404

    def test_extension_string_entry(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(
            packages_mod, "get_package_modules", lambda n, k: ["just-a-string"]
        )
        body = _client().get(PREFIX + "/pkg/tools").json()
        assert body[0]["name"] == "just-a-string"
        assert body[0]["module"] is None

    def test_extension_dict_with_class_name(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(
            packages_mod,
            "get_package_modules",
            lambda n, k: [{"name": "x", "class_name": "Foo"}],
        )
        body = _client().get(PREFIX + "/pkg/plugins").json()
        assert body[0]["class"] == "Foo"

    def test_extension_skill_kind(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(
            packages_mod,
            "get_package_modules",
            lambda n, k: [{"name": "skill1"}],
        )
        body = _client().get(PREFIX + "/pkg/skills").json()
        assert body[0]["path"] == ""

    def test_extension_non_list_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(packages_mod, "get_package_modules", lambda n, k: "garbage")
        body = _client().get(PREFIX + "/pkg/io").json()
        assert body == []

    def test_extension_non_dict_entry(self, monkeypatch, tmp_path):
        monkeypatch.setattr(packages_mod, "get_package_root", lambda n: tmp_path)
        monkeypatch.setattr(packages_mod, "get_package_modules", lambda n, k: [42])
        body = _client().get(PREFIX + "/pkg/triggers").json()
        assert body[0]["name"] == "42"
