"""``/api/app/shims``: opt-in terminal command for desktop installs."""

import sys

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from kohakuterrarium.api.routes import app_shims
from kohakuterrarium.cli import shims


@pytest.fixture
def home(monkeypatch, tmp_path):
    """Per-user locations under tmp_path and a recorded PATH."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("KT_PYTHON", sys.executable)
    monkeypatch.setenv("KT_BOOTSTRAP_DIR", str(tmp_path / "bootstrap"))
    path = {"on": False, "calls": []}
    monkeypatch.setattr(app_shims, "is_on_user_path", lambda directory: path["on"])
    monkeypatch.setattr(
        app_shims,
        "add_to_user_path",
        lambda directory: path.update(on=True)
        or path["calls"].append(("add", directory)),
    )
    monkeypatch.setattr(
        app_shims,
        "remove_from_user_path",
        lambda directory: path.update(on=False)
        or path["calls"].append(("remove", directory)),
    )
    return path


def _client(address="127.0.0.1"):
    app = FastAPI()
    app.include_router(app_shims.router, prefix="/api/app")
    return TestClient(app, client=(address, 5000))


def test_status_reports_nothing_installed_before_the_user_opts_in(home):
    body = _client().get("/api/app/shims").json()

    assert body["supported"] is True and body["local"] is True
    assert body["mode"] == shims.MODE_RUNTIME
    assert body["installed"] == [] and body["on_path"] is False
    assert body["commands"] == ["kt", "kt-cli", "kt-tui"]


def test_install_writes_the_wrappers_and_adds_the_directory_to_path(home):
    client = _client()

    body = client.post("/api/app/shims/install", json={"add_to_path": True}).json()

    assert body["installed"] == ["kt", "kt-cli", "kt-tui"]
    assert body["on_path"] is True
    assert [call[0] for call in home["calls"]] == ["add"]
    assert home["calls"][0][1] == shims.build_plan().target_dir
    assert client.get("/api/app/shims").json()["installed"] == body["installed"]


def test_install_can_leave_path_alone(home):
    body = _client().post("/api/app/shims/install", json={"add_to_path": False}).json()

    assert body["installed"] and body["on_path"] is False
    assert home["calls"] == []


def test_uninstall_removes_only_our_wrappers_and_the_path_entry(home, tmp_path):
    client = _client()
    client.post("/api/app/shims/install", json={"add_to_path": True})
    plan = shims.build_plan()
    foreign = shims.shim_path(plan, "kt")
    foreign.unlink()
    foreign.write_text("someone else's kt\n", encoding="utf-8")

    body = client.post(
        "/api/app/shims/uninstall", json={"remove_from_path": True}
    ).json()

    assert body["removed"] == ["kt-cli", "kt-tui"]
    assert foreign.read_text(encoding="utf-8") == "someone else's kt\n"
    assert body["on_path"] is False
    assert home["calls"][-1][0] == "remove"


def test_remote_clients_cannot_change_the_machine(home):
    remote = _client("203.0.113.9")

    assert remote.post("/api/app/shims/install", json={}).status_code == 403
    assert remote.post("/api/app/shims/uninstall", json={}).status_code == 403
    assert home["calls"] == []
    status = remote.get("/api/app/shims")
    assert status.status_code == 200 and status.json()["local"] is False


def test_pip_installs_on_windows_are_pointed_at_pipx(home, monkeypatch):
    monkeypatch.delenv("KT_PYTHON")
    monkeypatch.setattr(shims.platform, "system", lambda: "Windows")
    monkeypatch.setattr(shims, "in_bundle", lambda: False)

    client = _client()

    assert client.get("/api/app/shims").json()["supported"] is False
    assert client.post("/api/app/shims/install", json={}).status_code == 409
