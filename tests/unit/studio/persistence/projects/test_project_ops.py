"""Unit tests for ``studio.persistence.projects.ops`` over a real index."""

import json
import os

import pytest

from kohakuterrarium.errors import InvalidRequestError, NotFoundError, ProjectsFileError
from kohakuterrarium.studio.persistence.projects import paths as paths_mod
from kohakuterrarium.studio.persistence.projects.ops import (
    add_project,
    annotate_rows,
    forget_project,
    list_projects,
    live_locations,
    session_scope,
    update_project,
)
from kohakuterrarium.studio.persistence.projects.paths import (
    normalize_path,
    project_id,
)
from kohakuterrarium.studio.persistence.projects.store import (
    load_records,
    projects_path,
)
from kohakuterrarium.studio.persistence.session_index.entry import (
    SessionIndexEntry,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex


def local_id(folder) -> str:
    """The id of the project at a local folder, keyed the way the code keys it."""
    return project_id("", normalize_path(str(folder), "")[1])


@pytest.fixture(autouse=True)
def host_local_paths(monkeypatch):
    """Local folders here are real temp folders, so they follow this host's rules."""
    monkeypatch.setattr(paths_mod, "LOCAL_WINDOWS", os.name == "nt")


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


@pytest.fixture
def index(tmp_path):
    instance = SessionIndex(tmp_path / "sessions" / ".kt-index.kvault")
    try:
        yield instance
    finally:
        instance.close()


def _session(index, name, pwd, *, node_id="", last_active="2026-01-01"):
    index.upsert(
        SessionIndexEntry(
            filename=f"{name}.kohakutr",
            name=name,
            file_mtime=1.0,
            file_size=1,
            preview="",
            config_path="",
            agents=["a"],
            pwd=pwd,
            config_type="agent",
            status="paused",
            last_active=last_active,
            created_at=last_active,
            format_version=2,
            node_id=node_id,
        )
    )


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo-x"
    (folder / "frontend").mkdir(parents=True)
    return folder


class TestList:
    def test_found_projects_and_unassigned_tally(self, config, index, repo):
        _session(index, "s1", str(repo), last_active="2026-02-01")
        _session(index, "s2", str(repo / "frontend"), last_active="2026-03-01")
        _session(index, "old", "")
        result = list_projects(index, live=[("_host", str(repo / "frontend"))])
        assert result["unassigned"] == {
            "session_count": 1,
            "running_count": 0,
            "last_active": "2026-01-01",
        }
        assert result["projects"] == [
            {
                "id": local_id(repo),
                "path": str(repo),
                "node": None,
                "name": "repo-x",
                "pinned": False,
                "hidden": False,
                "saved": False,
                "exists": True,
                "session_count": 2,
                "running_count": 1,
                "last_active": "2026-03-01",
            }
        ]
        assert not projects_path().exists()

    def test_users_see_their_own_saved_projects(self, config, index, repo):
        add_project(index, str(repo), name="Mine", user_id=1)
        assert list_projects(index, user_id=1)["projects"][0]["name"] == "Mine"
        assert list_projects(index, user_id=2)["projects"] == []


class TestAdd:
    def test_add_saves_and_returns_the_project(self, config, index, repo):
        project = add_project(index, str(repo) + "/", name="  X  ")
        assert (project["path"], project["name"], project["saved"]) == (
            str(repo),
            "X",
            True,
        )
        record = load_records()[0]
        assert record["path"] == str(repo)
        assert record["node"] is None
        assert record["added_at"]

    def test_adding_again_returns_the_same_project(self, config, index, repo):
        first = add_project(index, str(repo), name="One")
        update_project(index, first["id"], {"hidden": True})
        again = add_project(index, str(repo))
        assert again["id"] == first["id"]
        assert again["name"] == "One"
        assert again["hidden"] is False
        assert len(load_records()) == 1
        renamed = add_project(index, str(repo), name="Two")
        assert renamed["name"] == "Two"

    def test_missing_local_folder_is_rejected(self, config, index, tmp_path):
        with pytest.raises(InvalidRequestError, match="does not exist"):
            add_project(index, str(tmp_path / "nope"))
        assert not projects_path().exists()

    @pytest.mark.parametrize("path", ["", "   ", "relative/dir"])
    def test_empty_or_relative_local_path_is_rejected(self, config, index, path):
        with pytest.raises(InvalidRequestError):
            add_project(index, path)

    def test_remote_folder_is_not_checked_on_this_host(self, config, index):
        project = add_project(index, "/srv/app/", node="w1")
        assert (project["node"], project["exists"], project["path"]) == (
            "w1",
            None,
            "/srv/app",
        )
        assert project["id"] == project_id("w1", "/srv/app")

    def test_non_text_name_is_rejected(self, config, index, repo):
        with pytest.raises(InvalidRequestError):
            add_project(index, str(repo), name=3)


class TestUpdate:
    def test_patching_a_found_project_saves_it_with_the_same_id(
        self, config, index, repo
    ):
        _session(index, "s1", str(repo))
        pid = local_id(repo)
        project = update_project(index, pid, {"pinned": True})
        assert (project["id"], project["saved"], project["pinned"]) == (
            pid,
            True,
            True,
        )
        assert project["session_count"] == 1
        assert [r["id"] for r in load_records()] == [pid]

    def test_only_sent_fields_change(self, config, index, repo):
        pid = add_project(index, str(repo), name="Keep")["id"]
        update_project(index, pid, {"pinned": True})
        project = update_project(index, pid, {"hidden": True})
        assert (project["name"], project["pinned"], project["hidden"]) == (
            "Keep",
            True,
            True,
        )

    def test_null_name_restores_the_default(self, config, index, repo):
        pid = add_project(index, str(repo), name="Custom")["id"]
        assert update_project(index, pid, {"name": None})["name"] == "repo-x"
        assert load_records()[0]["name"] is None

    def test_unknown_field_is_rejected(self, config, index, repo):
        pid = add_project(index, str(repo))["id"]
        with pytest.raises(InvalidRequestError, match="pin"):
            update_project(index, pid, {"pin": True})
        assert load_records()[0]["pinned"] is False

    def test_unknown_id_is_not_found_and_writes_nothing(self, config, index):
        with pytest.raises(NotFoundError):
            update_project(index, "p_000000000000", {"pinned": True})
        assert not projects_path().exists()


class TestForget:
    def test_forget_removes_only_the_record(self, config, index, repo):
        _session(index, "s1", str(repo))
        pid = local_id(repo)
        update_project(index, pid, {"hidden": True, "name": "Gone"})
        assert list_projects(index)["projects"] == []
        assert forget_project(pid) == {"status": "forgotten", "id": pid}
        again = list_projects(index)["projects"]
        assert [(p["id"], p["saved"], p["hidden"], p["name"]) for p in again] == [
            (pid, False, False, "repo-x")
        ]
        assert index.count() == 1

    def test_forgetting_an_unsaved_id_is_not_found(self, config, index, repo):
        _session(index, "s1", str(repo))
        with pytest.raises(NotFoundError):
            forget_project(local_id(repo))
        assert not projects_path().exists()

    def test_forget_keeps_unknown_fields_of_other_records(self, config, index, repo):
        pid = add_project(index, str(repo))["id"]
        add_project(index, "/srv", node="w1")
        document = json.loads(projects_path().read_text())
        document["projects"][1]["color"] = "blue"
        projects_path().write_text(json.dumps(document))
        forget_project(pid)
        remaining = json.loads(projects_path().read_text())["projects"]
        assert [(r["path"], r["color"]) for r in remaining] == [("/srv", "blue")]


class TestSessionScope:
    def test_filter_and_annotation(self, config, index, repo, tmp_path):
        _session(index, "a", str(repo))
        _session(index, "b", str(repo / "frontend"))
        _session(index, "c", str(tmp_path / "other"))
        _session(index, "d", "")
        pid = local_id(repo)
        view, locations = session_scope(index, pid)
        page = index.list(locations=locations, sort="name", order="asc")
        annotate_rows(view, page.rows)
        assert [r["name"] for r in page.rows] == ["a", "b"]
        assert {r["project"]["id"] for r in page.rows} == {pid}
        assert page.rows[0]["project"]["name"] == "repo-x"

        view, locations = session_scope(index, "none")
        page = index.list(locations=locations)
        annotate_rows(view, page.rows)
        assert [(r["name"], r["project"]) for r in page.rows] == [("d", None)]

        view, locations = session_scope(index, "")
        assert locations is None
        view, locations = session_scope(index, "p_unknown")
        assert index.list(locations=locations).total == 0

    def test_saved_name_reaches_session_rows(self, config, index, repo):
        _session(index, "a", str(repo / "frontend"))
        add_project(index, str(repo), name="Repo X", user_id=5)
        view, _ = session_scope(index, None, user_id=5)
        rows = index.list().rows
        annotate_rows(view, rows)
        assert rows[0]["project"] == {
            "id": local_id(repo),
            "name": "Repo X",
        }


def test_live_locations_take_only_running_rows():
    rows = [
        {"is_live": True, "node_id": "_host", "pwd": "/a"},
        {"is_live": False, "node_id": "_host", "pwd": "/b"},
        {"is_live": True, "node_id": None, "pwd": None},
    ]
    assert live_locations(rows) == [("_host", "/a"), ("", "")]


def test_strict_view_raises_on_corrupt_file_and_lenient_view_ignores_it(
    config, index, repo
):
    _session(index, "a", str(repo))
    projects_path().parent.mkdir(parents=True, exist_ok=True)
    projects_path().write_text("[1]")
    with pytest.raises(ProjectsFileError):
        list_projects(index)
    view, _ = session_scope(index, None)
    assert [p["saved"] for p in view.projects()] == [False]
