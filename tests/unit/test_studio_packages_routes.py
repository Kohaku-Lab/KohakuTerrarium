"""Tests for the studio ``/api/studio/packages/*`` discovery routes.

Covers the H.4 / audit #18 expansion: per-kind discovery endpoints
for plugins, tools, triggers, io, skills, plus the package-summary
card shape. Uses a monkeypatched temp ``PACKAGES_DIR`` (matching the
pattern from ``test_package_extensions.py``) so tests don't depend
on whatever the developer happens to have installed.
"""

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.studio import build_studio_router
from kohakuterrarium.api.studio.deps import set_workspace
from kohakuterrarium.packages.install import install_package


@pytest.fixture
def tmp_packages(tmp_path, monkeypatch):
    """Redirect ``PACKAGES_DIR`` to a fresh temp dir for the test."""
    import kohakuterrarium.packages.locations as pkg_mod

    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    monkeypatch.setattr(pkg_mod, "PACKAGES_DIR", packages_dir)
    return packages_dir


@pytest.fixture
def extension_package(tmp_path):
    """A package declaring plugins + triggers + io, but no tools."""
    pkg = tmp_path / "ext-pack-src"
    pkg.mkdir()
    (pkg / "kohaku.yaml").write_text(
        yaml.dump(
            {
                "name": "ext-pack",
                "version": "0.2.0",
                "description": "Extension-only package",
                "plugins": [
                    {
                        "name": "my_plugin",
                        "module": "ext_pack.plugins.my_plugin",
                        "class_name": "MyPlugin",
                        "description": "A custom plugin",
                    },
                ],
                "triggers": [
                    {
                        "name": "my_cron",
                        "module": "ext_pack.triggers.cron",
                        "class": "CronTrigger",
                        "description": "Fire on cron schedule",
                    },
                ],
                "io": [
                    {
                        "name": "my_input",
                        "module": "ext_pack.io.my_input",
                        "class": "MyInput",
                        "description": "Custom input module",
                    },
                ],
                "python_dependencies": ["some-pkg>=1.0"],
            }
        )
    )
    return pkg


@pytest.fixture
def tools_package(tmp_path):
    """A package declaring tools (but no triggers/io) for the tools route."""
    pkg = tmp_path / "tools-pack-src"
    pkg.mkdir()
    (pkg / "kohaku.yaml").write_text(
        yaml.dump(
            {
                "name": "tools-pack",
                "version": "0.1.0",
                "description": "Tools-only package",
                "tools": [
                    {
                        "name": "my_tool",
                        "module": "tp.tools.my_tool",
                        "class_name": "MyTool",
                        "description": "A custom tool",
                    },
                ],
            }
        )
    )
    return pkg


@pytest.fixture
def studio_client():
    """Minimal FastAPI app with just the studio router — no workspace."""
    app = FastAPI()
    app.include_router(build_studio_router())
    set_workspace(None)
    with TestClient(app) as c:
        yield c
    set_workspace(None)


# ---------------------------------------------------------------------------
# 404 for missing packages
# ---------------------------------------------------------------------------


def test_summary_unknown_package_returns_404(tmp_packages, studio_client):
    resp = studio_client.get("/api/studio/packages/__no_such_pkg__")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_plugins_unknown_package_returns_404(tmp_packages, studio_client):
    resp = studio_client.get("/api/studio/packages/__no_such_pkg__/plugins")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-kind discovery endpoints
# ---------------------------------------------------------------------------


def test_plugins_returns_manifest_entries(
    tmp_packages, extension_package, studio_client
):
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    entry = body[0]
    assert entry["name"] == "my_plugin"
    assert entry["module"] == "ext_pack.plugins.my_plugin"
    # class_name is normalized to class for the UI
    assert entry["class"] == "MyPlugin"
    assert entry["description"] == "A custom plugin"


def test_tools_empty_when_manifest_has_no_tools(
    tmp_packages, extension_package, studio_client
):
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack/tools")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tools_returns_manifest_entries(tmp_packages, tools_package, studio_client):
    install_package(str(tools_package))
    resp = studio_client.get("/api/studio/packages/tools-pack/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "my_tool"
    assert body[0]["class"] == "MyTool"


def test_triggers_returns_manifest_entries(
    tmp_packages, extension_package, studio_client
):
    """Cross-check: an extension package ships a trigger and the /triggers
    endpoint returns it with the right shape."""
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack/triggers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "my_cron"
    assert body[0]["module"] == "ext_pack.triggers.cron"
    assert body[0]["class"] == "CronTrigger"


def test_io_returns_manifest_entries(tmp_packages, extension_package, studio_client):
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack/io")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "my_input"
    assert body[0]["module"] == "ext_pack.io.my_input"


def test_skills_returns_empty_when_not_declared(
    tmp_packages, extension_package, studio_client
):
    """Skills manifest slot is T2 (A.4) — today packages should return []."""
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack/skills")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Summary card
# ---------------------------------------------------------------------------


def test_summary_returns_counts(tmp_packages, extension_package, studio_client):
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages/ext-pack")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "ext-pack"
    assert body["version"] == "0.2.0"
    assert body["description"] == "Extension-only package"
    assert body["creatures"] == 0
    assert body["terrariums"] == 0
    assert body["plugins"] == 1
    assert body["tools"] == 0
    assert body["triggers"] == 1
    assert body["io"] == 1
    assert body["skills"] == 0
    assert body["has_python_dependencies"] is True
    assert "path" in body


def test_summary_tools_package(tmp_packages, tools_package, studio_client):
    install_package(str(tools_package))
    resp = studio_client.get("/api/studio/packages/tools-pack")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools"] == 1
    assert body["plugins"] == 0
    assert body["triggers"] == 0
    assert body["io"] == 0
    assert body["has_python_dependencies"] is False


# ---------------------------------------------------------------------------
# Backward compat — the legacy routes still work alongside the new ones.
# ---------------------------------------------------------------------------


def test_list_all_packages_still_works(tmp_packages, extension_package, studio_client):
    install_package(str(extension_package))
    resp = studio_client.get("/api/studio/packages")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "ext-pack"


def test_modules_route_still_404_for_unknown_package(tmp_packages, studio_client):
    resp = studio_client.get("/api/studio/packages/__no_such_pkg__/modules/tools")
    assert resp.status_code == 404
