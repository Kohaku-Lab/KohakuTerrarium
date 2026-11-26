"""Unit tests for ``studio.persistence.projects.membership``."""

import time

from kohakuterrarium.studio.persistence.projects.membership import (
    ProjectRoot,
    ProjectView,
    sort_projects,
)
from kohakuterrarium.studio.persistence.projects.paths import project_id


def _saved(path, node=None, **extra):
    return {"path": path, "node": node, **extra}


def _by_path(view, **kw):
    return {p["path"]: p for p in view.projects(**kw)}


class TestFoundProjects:
    def test_distinct_folders_are_found_projects(self):
        view = ProjectView(
            [], {("", "/a"): (2, "2026-01-02"), ("", "/b"): (1, "2026-01-01")}
        )
        projects = _by_path(view)
        assert sorted(projects) == ["/a", "/b"]
        assert projects["/a"]["saved"] is False
        assert projects["/a"]["session_count"] == 2
        assert projects["/a"]["id"] == project_id("", "/a")

    def test_subfolder_sessions_fold_into_a_known_parent(self):
        view = ProjectView(
            [],
            {
                ("", "/repo-x"): (1, "2026-01-01"),
                ("", "/repo-x/frontend"): (3, "2026-01-05"),
            },
        )
        projects = view.projects()
        assert [p["path"] for p in projects] == ["/repo-x"]
        assert projects[0]["session_count"] == 4
        assert projects[0]["last_active"] == "2026-01-05"
        assert view.ref_for("", "/repo-x/frontend")["id"] == project_id("", "/repo-x")

    def test_subfolder_without_known_parent_is_its_own_project(self):
        view = ProjectView([], {("", "/repo-x/frontend"): (1, "t")})
        assert [p["path"] for p in view.projects()] == ["/repo-x/frontend"]

    def test_trailing_separator_variants_are_one_project(self):
        view = ProjectView([], {("", "/a"): (1, "t1"), ("", "/a/"): (2, "t2")})
        projects = view.projects()
        assert len(projects) == 1
        assert projects[0]["session_count"] == 3

    def test_saved_parent_absorbs_subfolders(self):
        view = ProjectView([_saved("/repo")], {("", "/repo/api/v2"): (2, "t")})
        projects = view.projects()
        assert [(p["path"], p["saved"]) for p in projects] == [("/repo", True)]
        assert projects[0]["session_count"] == 2


class TestDeepestWins:
    def test_nested_saved_projects_take_their_own_sessions(self):
        groups = {
            ("", "/repo"): (1, "t1"),
            ("", "/repo/web"): (2, "t2"),
            ("", "/repo/web/src"): (4, "t3"),
            ("", "/repo/docs"): (8, "t4"),
        }
        view = ProjectView([_saved("/repo"), _saved("/repo/web")], groups)
        projects = _by_path(view)
        assert sorted(projects) == ["/repo", "/repo/web"]
        assert projects["/repo/web"]["session_count"] == 6
        assert projects["/repo"]["session_count"] == 9
        assert view.ref_for("", "/repo/web/src")["id"] == project_id("", "/repo/web")
        assert view.ref_for("", "/repo/docs")["id"] == project_id("", "/repo")

    def test_found_root_below_a_saved_one_is_not_created(self):
        view = ProjectView([_saved("/repo/web")], {("", "/repo"): (1, "t")})
        assert sorted(_by_path(view)) == ["/repo", "/repo/web"]
        assert view.ref_for("", "/repo/web/x")["id"] == project_id("", "/repo/web")


class TestNodes:
    def test_same_path_on_two_nodes_is_two_projects(self):
        view = ProjectView([], {("", "/srv"): (1, "t"), ("w1", "/srv"): (2, "t")})
        nodes = sorted((p["node"] or "", p["session_count"]) for p in view.projects())
        assert nodes == [("", 1), ("w1", 2)]

    def test_saved_local_project_does_not_absorb_remote_sessions(self):
        view = ProjectView([_saved("/srv")], {("w1", "/srv/app"): (1, "t")})
        projects = _by_path(view)
        assert projects["/srv"]["session_count"] == 0
        assert projects["/srv/app"]["node"] == "w1"

    def test_host_alias_counts_as_local(self):
        view = ProjectView([_saved("/srv")], {("_host", "/srv/x"): (1, "t")})
        assert view.projects()[0]["session_count"] == 1
        assert view.projects()[0]["node"] is None

    def test_remote_projects_report_unknown_existence(self, tmp_path):
        view = ProjectView([], {("w1", str(tmp_path)): (1, "t")})
        assert view.projects()[0]["exists"] is None

    def test_local_existence_is_checked(self, tmp_path):
        view = ProjectView(
            [], {("", str(tmp_path)): (1, "t"), ("", "/no/such/dir/x"): (1, "t")}
        )
        exists = {p["path"]: p["exists"] for p in view.projects()}
        assert exists == {str(tmp_path): True, "/no/such/dir/x": False}

    def test_remote_windows_paths_fold_case_insensitively(self):
        view = ProjectView(
            [], {("w1", "D:\\Work"): (1, "t"), ("w1", "d:/work/api"): (1, "t")}
        )
        projects = view.projects()
        assert [p["path"] for p in projects] == ["D:\\Work"]
        assert projects[0]["session_count"] == 2


class TestUnassignedAndLive:
    def test_sessions_without_folder_are_unassigned(self):
        view = ProjectView([], {("", ""): (5, "t9"), ("", "/a"): (1, "t1")})
        assert view.unassigned.public_counts() == {
            "session_count": 5,
            "running_count": 0,
            "last_active": "t9",
        }
        assert view.ref_for("", "") is None
        assert view.locations_for("none") == {("", "")}

    def test_running_sessions_count_per_project(self):
        view = ProjectView(
            [],
            {("", "/a"): (1, "t")},
            live=[("_host", "/a/sub"), (None, None), ("", "/new")],
        )
        projects = _by_path(view)
        assert projects["/a"]["running_count"] == 1
        assert projects["/new"]["running_count"] == 1
        assert projects["/new"]["session_count"] == 0
        assert view.unassigned.running_count == 1

    def test_locations_list_raw_index_pairs(self):
        view = ProjectView(
            [], {("", "/a"): (1, "t"), ("", "/a/b/"): (1, "t"), ("", "/c"): (1, "t")}
        )
        pid = project_id("", "/a")
        assert view.locations_for(pid) == {("", "/a"), ("", "/a/b/")}
        assert view.locations_for("p_unknown") == set()


class TestSavedFields:
    def test_name_pinned_hidden_come_from_the_record(self):
        view = ProjectView(
            [_saved("/a", name=" Alpha ", pinned=True, hidden=False)],
            {},
        )
        project = view.projects()[0]
        assert (project["name"], project["pinned"], project["saved"]) == (
            "Alpha",
            True,
            True,
        )
        assert project["last_active"] is None

    def test_blank_name_falls_back_to_the_folder_name(self):
        view = ProjectView([_saved("/x/repo", name="  ")], {})
        assert view.projects()[0]["name"] == "repo"

    def test_hidden_projects_are_listed_only_on_request(self):
        view = ProjectView([_saved("/a", hidden=True)], {("", "/a/x"): (1, "t")})
        assert view.projects() == []
        assert view.projects(include_hidden=True)[0]["session_count"] == 1
        assert view.ref_for("", "/a/x")["id"] == project_id("", "/a")

    def test_duplicate_saved_keys_keep_the_first(self):
        view = ProjectView([_saved("/a", name="one"), _saved("/a/", name="two")], {})
        assert [p["name"] for p in view.projects()] == ["one"]

    def test_get_returns_the_root(self):
        view = ProjectView([_saved("/a")], {})
        root = view.get(project_id("", "/a"))
        assert isinstance(root, ProjectRoot) and root.saved is True
        assert view.get("p_missing") is None


class TestOrdering:
    def test_pinned_then_recent_then_name(self):
        rows = [
            {"id": "1", "name": "zeta", "pinned": False, "last_active": "2026-03"},
            {"id": "2", "name": "beta", "pinned": False, "last_active": None},
            {"id": "3", "name": "Alpha", "pinned": False, "last_active": None},
            {"id": "4", "name": "old", "pinned": True, "last_active": "2020-01"},
            {"id": "5", "name": "mid", "pinned": False, "last_active": "2026-02"},
        ]
        assert [r["id"] for r in sort_projects(rows)] == ["4", "1", "5", "3", "2"]


class TestScale:
    def test_many_sessions_group_quickly(self):
        groups = {}
        for repo in range(200):
            groups[("", f"/kt-scale/code/repo{repo}")] = (
                10,
                f"2026-01-{repo % 28:02d}",
            )
            for sub in range(20):
                groups[("", f"/kt-scale/code/repo{repo}/pkg{sub}/src")] = (5, "2025")
        groups[("", "")] = (1000, "2024")
        start = time.perf_counter()
        view = ProjectView([_saved("/kt-scale/code/repo7/pkg3")], groups)
        projects = view.projects()
        elapsed = time.perf_counter() - start
        assert len(projects) == 201
        assert sum(p["session_count"] for p in projects) == 200 * 10 + 200 * 20 * 5
        assert view.unassigned.session_count == 1000
        assert elapsed < 2.0
