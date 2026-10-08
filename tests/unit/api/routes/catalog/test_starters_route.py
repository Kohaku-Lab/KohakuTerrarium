"""``/starters``: the gallery of starting points and what each would write."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import _deps as catalog_deps
from kohakuterrarium.api.routes.catalog import starters as starters_mod
from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.studio.catalog.packages_scan import invalidate_scan_caches
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(starters_mod.router, prefix="/s")
    yield TestClient(app)
    catalog_deps.set_workspace(None)


def test_lists_starters_by_kind_and_refuses_unknown_kinds(client):
    every = client.get("/s").json()
    tools = client.get("/s", params={"kind": "tools"}).json()
    assert tools[0] == {
        "kind": "tools",
        "id": "blank",
        "label": "Return a value",
        "summary": tools[0]["summary"],
        "order": 0,
    }
    assert {s["kind"] for s in every} >= {"creatures", "tools", "plugins"}
    assert all(s in every for s in tools)
    r = client.get("/s", params={"kind": "widgets"})
    assert (r.status_code, r.json()["detail"]["code"]) == (400, "unknown_kind")


def test_module_preview_shows_files_ref_and_entry_without_writing(
    client, tmp_path, monkeypatch
):
    body = {"kind": "plugins", "id": "tool_guard", "name": "guard"}
    plain = client.post("/s/preview", json=body).json()
    assert plain["ref"] == "modules/plugins/guard.py"
    paths = [f["path"] for f in plain["files"]]
    assert paths == ["modules/plugins/guard.py", "modules/plugins/guard.schema.json"]
    assert '"blocked_tools"' in plain["files"][1]["content"]
    assert plain["entry"] == {
        "name": "guard",
        "type": "custom",
        "module": "modules/plugins/guard.py",
        "class": "GuardPlugin",
    }

    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", tmp_path / "packages")
    monkeypatch.setattr(loc_mod, "PROJECT_DIR", None)
    invalidate_scan_caches()
    root = loc_mod.ensure_local_project()
    catalog_deps.set_workspace(LocalWorkspace.open(root))
    sub = client.post("/s/preview", json={"kind": "subagents", "name": "look"}).json()
    assert sub["ref"] == "@/modules/subagents/look.py"
    assert sub["entry"]["config"] == "LOOK_CONFIG"
    assert not (root / "modules" / "subagents" / "look.py").exists()
    invalidate_scan_caches()


def test_creature_preview_and_refusals(client):
    r = client.post(
        "/s/preview",
        json={"kind": "creatures", "id": "coder", "name": "dev", "purpose": "Ships."},
    ).json()
    files = {f["path"]: f["content"] for f in r["files"]}
    assert set(files) == {
        "creatures/dev/config.yaml",
        "creatures/dev/prompts/system.md",
    }
    assert "- name: bash" in files["creatures/dev/config.yaml"]
    assert "Ships." in files["creatures/dev/prompts/system.md"]

    bad = client.post("/s/preview", json={"kind": "tools", "id": "nope", "name": "t"})
    assert (bad.status_code, bad.json()["detail"]["code"]) == (400, "unknown_starter")
    bad = client.post("/s/preview", json={"kind": "tools", "name": ".hidden"})
    assert (bad.status_code, bad.json()["detail"]["code"]) == (400, "invalid_name")
