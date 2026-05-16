"""Error-path coverage tests for studio.attach.workspace_files."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from kohakuterrarium.studio.attach import workspace_files as wf

# ── _validate_path error path ───────────────────────────────


class TestValidatePathError:
    def test_oserror_path_raises(self, monkeypatch):
        # Force Path.resolve to raise OSError on Windows.
        original_resolve = Path.resolve

        def _boom(self):
            raise OSError("bad path")

        monkeypatch.setattr(Path, "resolve", _boom)
        try:
            with pytest.raises(HTTPException) as exc:
                wf._validate_path("/some/path")
            assert exc.value.status_code == 400
        finally:
            monkeypatch.setattr(Path, "resolve", original_resolve)


# ── _list_browse_roots platform branches ────────────────────


class TestListBrowseRootsPlatform:
    def test_windows_branch(self, monkeypatch):
        # Force sys.platform = win32 and stub Path.exists so exactly the
        # C: and D: drive letters "exist".
        monkeypatch.setattr(wf.sys, "platform", "win32")

        original_exists = Path.exists

        def _exists(self):
            return str(self).rstrip("/").rstrip("\\").upper() in {"C:", "D:"}

        monkeypatch.setattr(Path, "exists", _exists)
        try:
            out = wf._list_browse_roots()
            # The win32 branch must enumerate the existing drive roots —
            # and only those.
            drives = {str(p).rstrip("/\\").upper() for p in out}
            assert drives == {"C:", "D:"}
        finally:
            monkeypatch.setattr(Path, "exists", original_exists)


# ── _has_visible_children error ─────────────────────────────


class TestHasVisibleChildrenError:
    def test_permission_error(self, monkeypatch, tmp_path):
        # Patch Path.iterdir to raise PermissionError.
        original = Path.iterdir

        def _boom(self):
            raise PermissionError("no access")

        monkeypatch.setattr(Path, "iterdir", _boom)
        try:
            assert wf._has_visible_children(tmp_path) is False
        finally:
            monkeypatch.setattr(Path, "iterdir", original)


# ── _build_tree permission error branch ─────────────────────


class TestBuildTreePermissionError:
    def test_permission_during_iterdir(self, monkeypatch, tmp_path):
        # Build a tree node where iterdir raises PermissionError.
        d = tmp_path / "sub"
        d.mkdir()
        (d / "f.txt").write_text("x")

        called = {"first": True}

        def _conditional(self):
            if str(self) == str(d) and called["first"]:
                called["first"] = False
                raise PermissionError("locked")
            return iter([])

        original = Path.iterdir
        monkeypatch.setattr(Path, "iterdir", _conditional)
        try:
            out = wf._build_tree(d, depth=2)
            assert "children" in out
        finally:
            monkeypatch.setattr(Path, "iterdir", original)


# ── _build_tree file size OSError ───────────────────────────


class TestBuildTreeFileStatError:
    def test_stat_raises(self, monkeypatch, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hello")
        from kohakuterrarium.studio.attach import workspace_files as m

        original_stat = Path.stat
        counter = {"n": 0}

        def _conditional(self, *args, **kwargs):
            counter["n"] += 1
            # First two calls (is_file via is_dir + is_file checks)
            # need to succeed. Only after the file has been resolved
            # as a "file", _build_tree calls path.stat() once more for
            # size — fail then.
            if counter["n"] <= 2:
                return original_stat(self, *args, **kwargs)
            raise OSError("can't stat")

        monkeypatch.setattr(Path, "stat", _conditional)
        try:
            out = m._build_tree(f, depth=0)
            assert out["size"] == 0
        finally:
            monkeypatch.setattr(Path, "stat", original_stat)


# ── browse_directories permission error during iterdir ─────


class TestBrowseDirectoriesPermissionError:
    async def test_permission_error_during_walk(self, monkeypatch, tmp_path):
        (tmp_path / "sub").mkdir()

        def _boom(self):
            raise PermissionError("locked")

        original = Path.iterdir
        monkeypatch.setattr(Path, "iterdir", _boom)
        try:
            out = await wf.browse_directories(str(tmp_path))
            assert out["directories"] == []
        finally:
            monkeypatch.setattr(Path, "iterdir", original)


# ── read_file error branches ────────────────────────────────


class TestReadFileErrors:
    async def test_permission_denied(self, monkeypatch, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")

        def _boom(self, *a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "read_text", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.read_file(str(f))
        assert exc.value.status_code == 400

    async def test_oserror_500(self, monkeypatch, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")

        def _boom(self, *a, **kw):
            raise OSError("disk")

        monkeypatch.setattr(Path, "read_text", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.read_file(str(f))
        assert exc.value.status_code == 500


# ── write_file error branches ──────────────────────────────


class TestWriteFileErrors:
    async def test_permission_denied(self, monkeypatch, tmp_path):
        def _boom(self, *a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "write_text", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.write_file(str(tmp_path / "x.txt"), "data")
        assert exc.value.status_code == 400

    async def test_oserror_500(self, monkeypatch, tmp_path):
        def _boom(self, *a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.write_file(str(tmp_path / "x.txt"), "data")
        assert exc.value.status_code == 500


# ── rename_file error branches ─────────────────────────────


class TestRenameFileErrors:
    async def test_permission_denied(self, monkeypatch, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"

        def _boom(self, *a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "rename", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.rename_file(str(a), str(b))
        assert exc.value.status_code == 400

    async def test_oserror_500(self, monkeypatch, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"

        def _boom(self, *a, **kw):
            raise OSError("disk")

        monkeypatch.setattr(Path, "rename", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.rename_file(str(a), str(b))
        assert exc.value.status_code == 500


# ── delete_file error branches ─────────────────────────────


class TestDeleteFileErrors:
    async def test_permission_denied_on_file(self, monkeypatch, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")

        def _boom(self, *a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.delete_file(str(f))
        assert exc.value.status_code == 400

    async def test_oserror_500_on_file(self, monkeypatch, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("x")

        def _boom(self, *a, **kw):
            raise OSError("disk")

        monkeypatch.setattr(Path, "unlink", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.delete_file(str(f))
        assert exc.value.status_code == 500


# ── make_directory error branches ──────────────────────────


class TestMakeDirectoryErrors:
    async def test_permission_denied(self, monkeypatch, tmp_path):
        def _boom(self, *a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "mkdir", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.make_directory(str(tmp_path / "newd"))
        assert exc.value.status_code == 400

    async def test_oserror_500(self, monkeypatch, tmp_path):
        def _boom(self, *a, **kw):
            raise OSError("disk")

        monkeypatch.setattr(Path, "mkdir", _boom)
        with pytest.raises(HTTPException) as exc:
            await wf.make_directory(str(tmp_path / "newd"))
        assert exc.value.status_code == 500
