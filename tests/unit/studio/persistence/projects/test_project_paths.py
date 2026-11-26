"""Unit tests for ``studio.persistence.projects.paths``."""

import hashlib
import os

import pytest

from kohakuterrarium.studio.persistence.projects import paths as paths_mod
from kohakuterrarium.studio.persistence.projects.paths import (
    ancestors,
    clean_path,
    default_name,
    is_windows_path,
    normalize_node,
    normalize_path,
    path_key,
    project_id,
    public_node,
)


class TestNodes:
    @pytest.mark.parametrize("raw", [None, "", "  ", "_host"])
    def test_this_host_spellings_become_empty(self, raw):
        assert normalize_node(raw) == ""
        assert public_node(normalize_node(raw)) is None

    def test_worker_id_is_kept(self):
        assert normalize_node(" worker-1 ") == "worker-1"
        assert public_node("worker-1") == "worker-1"


class TestPosix:
    def test_trailing_separators_are_stripped(self):
        assert normalize_path("/home/u/repo///", "", windows=False) == (
            "/home/u/repo",
            "/home/u/repo",
        )

    def test_root_is_kept(self):
        assert clean_path("/", "", windows=False) == "/"
        assert clean_path("///", "", windows=False) == "/"

    def test_empty_stays_empty(self):
        assert normalize_path("   ", "", windows=False) == ("", "")

    def test_local_path_is_user_expanded(self):
        home = os.path.expanduser("~")
        assert clean_path("~/repo/", "", windows=False) == home + "/repo"

    def test_remote_path_is_not_user_expanded(self):
        assert clean_path("~/repo", "w1") == "~/repo"

    def test_case_is_kept_on_posix(self):
        assert path_key("/Home/Repo", "", windows=False) == "/Home/Repo"

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges")
    def test_symlinks_are_not_resolved(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert clean_path(str(link), "", windows=False) == str(link)

    def test_ancestors_nearest_first_to_root(self):
        assert ancestors("/a/b/c", "", windows=False) == ["/a/b", "/a", "/"]
        assert ancestors("/", "", windows=False) == []

    def test_default_name_is_last_segment(self):
        assert default_name("/home/u/repo-x", "", windows=False) == "repo-x"
        assert default_name("/", "", windows=False) == "/"


class TestWindows:
    def test_separators_unified_and_case_folded_in_the_key(self):
        display, key = normalize_path("C:/Users/Me/Repo/", "", windows=True)
        assert display == "C:\\Users\\Me\\Repo"
        assert key == "c:\\users\\me\\repo"

    def test_two_spellings_share_one_key_and_id(self):
        a = normalize_path("C:\\Users\\Me\\Repo", "", windows=True)[1]
        b = normalize_path("c:/users/me/repo\\", "", windows=True)[1]
        assert a == b
        assert project_id("", a) == project_id("", b)

    def test_drive_root_is_kept(self):
        assert clean_path("C:\\", "", windows=True) == "C:\\"
        assert clean_path("C:", "", windows=True) == "C:\\"
        assert clean_path("D:/", "", windows=True) == "D:\\"

    def test_ancestors_stop_at_drive_root(self):
        assert ancestors("c:\\a\\b", "", windows=True) == ["c:\\a", "c:\\"]

    def test_unc_ancestors_drop_trailing_separator(self):
        assert ancestors("\\\\srv\\share\\x", "", windows=True) == ["\\\\srv\\share"]

    def test_default_name_uses_backslash_segments(self):
        assert default_name("C:\\code\\repo", "", windows=True) == "repo"
        assert default_name("C:\\", "", windows=True) == "C:\\"

    def test_remote_shape_detection(self):
        assert is_windows_path("D:\\work", "w1") is True
        assert is_windows_path("\\\\srv\\share", "w1") is True
        assert is_windows_path("/srv/work", "w1") is False

    def test_local_shape_follows_this_host(self, monkeypatch):
        monkeypatch.setattr(paths_mod, "LOCAL_WINDOWS", True)
        assert normalize_path("C:/Repo", "")[1] == "c:\\repo"
        monkeypatch.setattr(paths_mod, "LOCAL_WINDOWS", False)
        assert normalize_path("/Repo", "")[1] == "/Repo"

    def test_remote_windows_key_is_case_folded(self):
        assert normalize_path("D:\\Work\\", "w1") == ("D:\\Work", "d:\\work")


class TestProjectId:
    def test_id_is_sha1_prefix_of_node_and_key(self):
        digest = hashlib.sha1(b"w1\0/srv/app").hexdigest()[:12]
        assert project_id("w1", "/srv/app") == "p_" + digest

    def test_id_separates_nodes(self):
        assert project_id("", "/srv/app") != project_id("w1", "/srv/app")

    def test_id_is_stable_across_calls(self):
        assert project_id("", "/a") == project_id("", "/a")
