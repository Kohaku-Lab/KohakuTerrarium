"""Unit tests for :mod:`kohakuterrarium.studio.attach.workspace_files`."""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from kohakuterrarium.studio.attach import workspace_files as wf

# ── pure helpers ────────────────────────────────────────────


class TestValidatePath:
    def test_valid(self, tmp_path):
        out = wf._validate_path(str(tmp_path))
        assert out == tmp_path.resolve()


class TestParentDirectory:
    def test_normal(self, tmp_path):
        out = wf._parent_directory(tmp_path / "x")
        assert out == str(tmp_path)

    def test_root_returns_none(self):
        # Filesystem root has no parent (parent == itself).
        if sys.platform == "win32":
            root = Path("C:/")
        else:
            root = Path("/")
        # parent of "/" is "/" itself.
        out = wf._parent_directory(root)
        assert out is None


class TestShouldSkip:
    def test_known_skip(self):
        assert wf._should_skip(".git")
        assert wf._should_skip("__pycache__")
        assert wf._should_skip("node_modules")

    def test_egg_info_suffix(self):
        assert wf._should_skip("project.egg-info")

    def test_not_skipped(self):
        assert not wf._should_skip("src")


class TestDirEntry:
    def test_directory(self, tmp_path):
        out = wf._dir_entry(tmp_path)
        assert out["type"] == "directory"

    def test_file(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        out = wf._dir_entry(f)
        assert out["type"] == "file"


class TestHasVisibleChildren:
    def test_empty(self, tmp_path):
        assert wf._has_visible_children(tmp_path) is False

    def test_has_child(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        assert wf._has_visible_children(tmp_path) is True

    def test_all_skipped(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "__pycache__").mkdir()
        assert wf._has_visible_children(tmp_path) is False


class TestBuildTree:
    def test_file_node(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hello")
        out = wf._build_tree(f, depth=2)
        assert out["type"] == "file"
        assert out["size"] == 5

    def test_dir_node_depth_zero(self, tmp_path):
        out = wf._build_tree(tmp_path, depth=0)
        assert "children" not in out

    def test_dir_with_children(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        out = wf._build_tree(tmp_path, depth=2)
        assert "children" in out
        assert out["has_children"] is True

    def test_skips_filtered_entries(self, tmp_path):
        (tmp_path / "real.txt").write_text("x")
        (tmp_path / ".git").mkdir()
        out = wf._build_tree(tmp_path, depth=1)
        names = [c["name"] for c in out["children"]]
        assert ".git" not in names
        assert "real.txt" in names


class TestDetectLanguage:
    def test_python(self):
        assert wf._detect_language(Path("x.py")) == "python"

    def test_yaml(self):
        assert wf._detect_language(Path("x.yaml")) == "yaml"

    def test_dockerfile(self):
        assert wf._detect_language(Path("Dockerfile")) == "dockerfile"

    def test_makefile(self):
        assert wf._detect_language(Path("makefile")) == "makefile"

    def test_cmake(self):
        assert wf._detect_language(Path("CMakeLists.txt")) == "cmake"

    def test_unknown(self):
        assert wf._detect_language(Path("x.foobar")) == "plaintext"


# ── async handlers ──────────────────────────────────────────


class TestGetFileTree:
    async def test_not_directory(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(HTTPException) as exc:
            await wf.get_file_tree(str(f))
        assert exc.value.status_code == 400

    async def test_depth_floor(self, tmp_path):
        # depth < 1 → coerced to 1.
        out = await wf.get_file_tree(str(tmp_path), depth=0)
        assert out["type"] == "directory"

    async def test_normal(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        out = await wf.get_file_tree(str(tmp_path))
        assert any(c["name"] == "f.txt" for c in out["children"])


class TestBrowseDirectories:
    async def test_no_path_returns_roots(self):
        out = await wf.browse_directories(None)
        assert out["current"] is None
        assert out["roots"]

    async def test_unknown_path(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.browse_directories(str(tmp_path / "ghost"))
        assert exc.value.status_code == 404

    async def test_not_directory(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        with pytest.raises(HTTPException) as exc:
            await wf.browse_directories(str(f))
        assert exc.value.status_code == 400

    async def test_with_path(self, tmp_path):
        (tmp_path / "sub1").mkdir()
        (tmp_path / "sub2").mkdir()
        (tmp_path / "file.txt").write_text("x")
        out = await wf.browse_directories(str(tmp_path))
        names = [d["name"] for d in out["directories"]]
        assert "sub1" in names
        assert "file.txt" not in names


class TestReadFile:
    async def test_not_found(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.read_file(str(tmp_path / "ghost"))
        assert exc.value.status_code == 404

    async def test_not_file(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.read_file(str(tmp_path))
        assert exc.value.status_code == 400

    async def test_binary_rejected(self, tmp_path):
        f = tmp_path / "img.bin"
        f.write_bytes(b"\xff\xfe\xfd\xfc")
        with pytest.raises(HTTPException) as exc:
            await wf.read_file(str(f))
        assert exc.value.status_code == 400

    async def test_normal(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hello", encoding="utf-8")
        out = await wf.read_file(str(f))
        assert out["content"] == "hello"
        assert out["language"] == "plaintext"


class TestWriteFile:
    async def test_writes(self, tmp_path):
        target = tmp_path / "sub" / "x.txt"
        out = await wf.write_file(str(target), "hello")
        assert out["success"] is True
        assert target.read_text() == "hello"


class TestRenameFile:
    async def test_source_missing(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.rename_file(str(tmp_path / "ghost"), str(tmp_path / "new"))
        assert exc.value.status_code == 404

    async def test_dest_exists(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"
        b.write_text("y")
        with pytest.raises(HTTPException) as exc:
            await wf.rename_file(str(a), str(b))
        assert exc.value.status_code == 400

    async def test_success(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"
        out = await wf.rename_file(str(a), str(b))
        assert out["success"]
        assert b.exists()


class TestDeleteFile:
    async def test_not_found(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.delete_file(str(tmp_path / "ghost"))
        assert exc.value.status_code == 404

    async def test_delete_file(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        await wf.delete_file(str(f))
        assert not f.exists()

    async def test_delete_dir(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        (d / "f.txt").write_text("x")
        await wf.delete_file(str(d))
        assert not d.exists()


class TestMakeDirectory:
    async def test_already_exists(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await wf.make_directory(str(tmp_path))
        assert exc.value.status_code == 400

    async def test_creates(self, tmp_path):
        new_dir = tmp_path / "newd"
        out = await wf.make_directory(str(new_dir))
        assert out["success"]
        assert new_dir.is_dir()


# ── _list_browse_roots ─────────────────────────────────────


class TestListBrowseRoots:
    def test_returns_paths(self):
        out = wf._list_browse_roots()
        assert out
        assert all(isinstance(p, Path) for p in out)
