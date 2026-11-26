"""Unit tests for ``/api/projects`` and the project fields of the session lists."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence import open_sessions, projects, saved
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.projects import (
    normalize_path,
    project_id,
    projects_path,
)
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.terrarium.service.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from kohakuterrarium.studio.persistence.session_index import close_session_index

pytestmark = pytest.mark.timeout(30)


def _pid(path) -> str:
    return project_id("", normalize_path(str(path), "")[1])


def _make_session(session_dir, name, pwd, *, conversation_open=False):
    store = SessionStore(str(session_dir / f"{name}.kohakutr"))
    try:
        store.init_meta("sid-" + name, "agent", "/cfg.yaml", pwd, ["alice"])
        if conversation_open:
            store.set_conversation_open(True)
    finally:
        store.close(update_status=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "cfg"))
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    repo = tmp_path / "repo-x"
    (repo / "frontend").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    yield {"sessions": session_dir, "repo": repo, "other": other}
    close_session_index()


def _client(session_dir, service=None) -> TestClient:
    service = service or LocalTerrariumService(Terrarium())
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[resolve_request_session_dir] = lambda: session_dir
    app.include_router(projects.router, prefix="/api/projects")
    app.include_router(open_sessions.router, prefix="/api/sessions")
    app.include_router(saved.router, prefix="/api/sessions")
    return TestClient(app)


def _seed(env):
    _make_session(env["sessions"], "s-root", str(env["repo"]))
    _make_session(env["sessions"], "s-front", str(env["repo"] / "frontend"))
    _make_session(env["sessions"], "s-other", str(env["other"]))
    _make_session(env["sessions"], "s-none", "")


class TestListRoute:
    def test_lists_found_projects_with_counts(self, env):
        _seed(env)
        body = _client(env["sessions"]).get("/api/projects").json()
        by_path = {p["path"]: p for p in body["projects"]}
        assert sorted(by_path) == sorted([str(env["repo"]), str(env["other"])])
        repo = by_path[str(env["repo"])]
        assert repo["id"] == _pid(env["repo"])
        assert (repo["session_count"], repo["running_count"]) == (2, 0)
        assert (repo["saved"], repo["exists"], repo["node"]) == (False, True, None)
        assert repo["last_active"]
        assert body["unassigned"]["session_count"] == 1
        assert body["unassigned"]["running_count"] == 0
        assert set(repo) == {
            "id",
            "path",
            "node",
            "name",
            "pinned",
            "hidden",
            "saved",
            "exists",
            "session_count",
            "running_count",
            "last_active",
        }

    def test_hidden_projects_need_include_hidden(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["other"])
        assert client.patch(f"/api/projects/{pid}", json={"hidden": True}).json()[
            "hidden"
        ]
        listed = [p["id"] for p in client.get("/api/projects").json()["projects"]]
        assert pid not in listed
        everything = client.get("/api/projects?include_hidden=true").json()
        assert pid in [p["id"] for p in everything["projects"]]

    def test_pinned_project_sorts_first(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["other"])
        client.patch(f"/api/projects/{pid}", json={"pinned": True})
        assert client.get("/api/projects").json()["projects"][0]["id"] == pid

    def test_corrupt_projects_file_is_a_server_error(self, env):
        projects_path().parent.mkdir(parents=True, exist_ok=True)
        projects_path().write_text("{oops")
        response = _client(env["sessions"]).get("/api/projects")
        assert response.status_code == 500
        assert "not valid JSON" in response.json()["detail"]
        assert projects_path().read_text() == "{oops"

    async def test_running_sessions_count_toward_their_project(self, env):
        engine = await TestTerrariumBuilder().with_creature("alice").build()
        service = LocalTerrariumService(engine)
        graph_id = engine.get_creature("alice").graph_id
        lifecycle.meta_for(service)[graph_id] = {
            "name": "live chat",
            "pwd": str(env["repo"] / "frontend"),
        }
        try:
            _make_session(env["sessions"], "s-root", str(env["repo"]))
            client = _client(env["sessions"], service)
            body = client.get("/api/projects").json()
            repo = body["projects"][0]
            assert (repo["path"], repo["running_count"]) == (str(env["repo"]), 1)
            rows = client.get("/api/sessions/open").json()
            live = [row for row in rows if row["is_live"]]
            assert live[0]["project"] == {"id": repo["id"], "name": "repo-x"}
        finally:
            await engine.shutdown()


class TestMutationRoutes:
    def test_add_rename_reset_and_forget(self, env):
        _seed(env)
        client = _client(env["sessions"])
        created = client.post(
            "/api/projects", json={"path": str(env["repo"]), "name": "Repo X"}
        )
        assert created.status_code == 200
        project = created.json()
        assert (project["name"], project["saved"], project["session_count"]) == (
            "Repo X",
            True,
            2,
        )
        again = client.post("/api/projects", json={"path": str(env["repo"]) + "/"})
        assert again.json()["id"] == project["id"]
        assert again.json()["name"] == "Repo X"

        reset = client.patch(f"/api/projects/{project['id']}", json={"name": None})
        assert reset.json()["name"] == "repo-x"

        forgotten = client.delete(f"/api/projects/{project['id']}")
        assert forgotten.json() == {"status": "forgotten", "id": project["id"]}
        listed = client.get("/api/projects").json()["projects"]
        assert {p["id"]: p["saved"] for p in listed}[project["id"]] is False

    def test_add_remote_project(self, env):
        client = _client(env["sessions"])
        body = client.post(
            "/api/projects", json={"path": "/srv/app", "node": "w1", "name": None}
        ).json()
        assert (body["node"], body["exists"], body["name"]) == ("w1", None, "app")

    def test_add_missing_local_folder_is_400(self, env):
        response = _client(env["sessions"]).post(
            "/api/projects", json={"path": str(env["repo"] / "nope")}
        )
        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]
        assert not projects_path().exists()

    def test_patch_found_project_saves_it(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["repo"])
        body = client.patch(f"/api/projects/{pid}", json={"name": "R"}).json()
        assert (body["id"], body["saved"], body["name"]) == (pid, True, "R")
        saved_file = json.loads(projects_path().read_text())
        assert [r["id"] for r in saved_file["projects"]] == [pid]
        assert saved_file["version"] == 1

    def test_patch_without_fields_changes_nothing_but_saves(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["repo"])
        client.patch(f"/api/projects/{pid}", json={"pinned": True})
        body = client.patch(f"/api/projects/{pid}", json={}).json()
        assert body["pinned"] is True

    def test_unknown_ids_are_404(self, env):
        client = _client(env["sessions"])
        assert client.patch("/api/projects/p_nope", json={}).status_code == 404
        assert client.delete("/api/projects/p_nope").status_code == 404

    def test_forget_found_only_project_is_404(self, env):
        _seed(env)
        pid = _pid(env["repo"])
        assert (
            _client(env["sessions"]).delete(f"/api/projects/{pid}").status_code == 404
        )

    def test_null_flag_is_rejected(self, env):
        _seed(env)
        pid = _pid(env["repo"])
        response = _client(env["sessions"]).patch(
            f"/api/projects/{pid}", json={"pinned": None}
        )
        assert response.status_code == 422


class TestSessionListProjectFields:
    def test_rows_carry_project_and_filter_by_it(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["repo"])
        client.patch(f"/api/projects/{pid}", json={"name": "Repo X"})

        everything = client.get("/api/sessions?limit=50").json()
        refs = {row["name"]: row["project"] for row in everything["sessions"]}
        assert refs["s-root"] == {"id": pid, "name": "Repo X"}
        assert refs["s-front"] == {"id": pid, "name": "Repo X"}
        assert refs["s-other"]["id"] == _pid(env["other"])
        assert refs["s-none"] is None

        only_repo = client.get(f"/api/sessions?project={pid}&sort=name&order=asc")
        assert [r["name"] for r in only_repo.json()["sessions"]] == [
            "s-front",
            "s-root",
        ]
        assert only_repo.json()["total"] == 2

        unassigned = client.get("/api/sessions?project=none").json()
        assert [r["name"] for r in unassigned["sessions"]] == ["s-none"]

        unknown = client.get("/api/sessions?project=p_nope").json()
        assert (unknown["sessions"], unknown["total"]) == ([], 0)

    def test_search_combines_with_project_filter(self, env):
        _seed(env)
        client = _client(env["sessions"])
        pid = _pid(env["other"])
        body = client.get(f"/api/sessions?project={pid}&search=alice").json()
        assert [r["name"] for r in body["sessions"]] == ["s-other"]

    def test_dormant_open_rows_carry_project(self, env):
        _make_session(
            env["sessions"], "dormant", str(env["repo"]), conversation_open=True
        )
        rows = _client(env["sessions"]).get("/api/sessions/open").json()
        assert rows[0]["project"] == {
            "id": _pid(env["repo"]),
            "name": "repo-x",
        }

    def test_corrupt_projects_file_does_not_break_session_lists(self, env):
        _seed(env)
        _make_session(
            env["sessions"], "dormant", str(env["repo"]), conversation_open=True
        )
        projects_path().parent.mkdir(parents=True, exist_ok=True)
        projects_path().write_text("{oops")
        client = _client(env["sessions"])
        listed = client.get("/api/sessions?limit=50")
        assert listed.status_code == 200
        refs = {row["name"]: row["project"] for row in listed.json()["sessions"]}
        assert refs["s-front"] == {"id": _pid(env["repo"]), "name": "repo-x"}
        open_rows = client.get("/api/sessions/open")
        assert open_rows.status_code == 200
        assert open_rows.json()[0]["project"]["id"] == _pid(env["repo"])
        assert client.get("/api/projects").status_code == 500
        assert projects_path().read_text() == "{oops"
