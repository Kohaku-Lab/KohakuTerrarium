"""venv_ops primitives — create / install / smoke / atomic_swap / rollback.

Each test exercises ONE primitive against the real filesystem
(``tmp_path``) but stubs heavy operations (``venv.EnvBuilder.create``,
``subprocess.run``) so the suite stays sub-second.
"""

from pathlib import Path

import pytest

from kohakuterrarium.launcher import venv_ops as _v


@pytest.fixture
def fake_venv(tmp_path):
    """Build a fake venv layout matching what create_venv would produce."""

    def _make(name="venv"):
        root = tmp_path / name
        bin_dir = root / ("Scripts" if _v.sys.platform == "win32" else "bin")
        bin_dir.mkdir(parents=True)
        # Touch the entry points venv_python / venv_kt expect to find.
        (
            bin_dir / ("python.exe" if _v.sys.platform == "win32" else "python")
        ).write_text("#!/usr/bin/env python\n")
        (bin_dir / ("kt.exe" if _v.sys.platform == "win32" else "kt")).write_text(
            "#!/usr/bin/env python\n"
        )
        return root

    return _make


class TestCreateVenv:
    def test_create_calls_envbuilder(self, tmp_path, monkeypatch):
        called = {}

        class _Builder:
            def __init__(self, **kw):
                called["kwargs"] = kw

            def create(self, path):
                called["path"] = path
                Path(path).mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(_v.venv, "EnvBuilder", _Builder)
        _v.create_venv(tmp_path / "v")
        assert called["kwargs"] == {
            "with_pip": True,
            "clear": True,
            "symlinks": False,
        }
        assert called["path"] == str(tmp_path / "v")

    def test_create_wraps_oserror(self, tmp_path, monkeypatch):
        class _Builder:
            def __init__(self, **kw):
                pass

            def create(self, path):
                raise OSError("disk full")

        monkeypatch.setattr(_v.venv, "EnvBuilder", _Builder)
        with pytest.raises(_v.VenvOpError, match="venv create failed"):
            _v.create_venv(tmp_path / "v")


class TestInstallInto:
    def test_install_invokes_pip(self, fake_venv, monkeypatch):
        venv = fake_venv()
        cmds = []

        def _fake_run(cmd, **kw):
            cmds.append(cmd)

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(_v.subprocess, "run", _fake_run)
        _v.install_into(venv, ["-U", "kohakuterrarium"])
        assert any("pip" in str(c) for c in cmds[0])
        assert "install" in cmds[0]
        assert "-U" in cmds[0]

    def test_install_failure_wraps(self, fake_venv, monkeypatch):
        venv = fake_venv()

        def _fake_run(cmd, **kw):
            import subprocess as _sp

            raise _sp.CalledProcessError(returncode=1, cmd=cmd)

        monkeypatch.setattr(_v.subprocess, "run", _fake_run)
        with pytest.raises(_v.VenvOpError, match="pip install failed"):
            _v.install_into(venv, ["-U", "kohakuterrarium"])

    def test_missing_python_raises(self, tmp_path):
        with pytest.raises(_v.VenvOpError, match="venv python missing"):
            _v.install_into(tmp_path / "no-such-venv", ["-U", "x"])


class TestAtomicSwap:
    def test_swap_promotes_new_and_stashes_current(self, tmp_path):
        current = tmp_path / "venv"
        current.mkdir()
        (current / "marker").write_text("old")
        new = tmp_path / "venv.new"
        new.mkdir()
        (new / "marker").write_text("new")
        backup = tmp_path / "venv.old"
        _v.atomic_swap(current, new, backup)
        assert backup.is_dir()
        assert (backup / "marker").read_text() == "old"
        assert current.is_dir()
        assert (current / "marker").read_text() == "new"
        assert not new.exists()

    def test_swap_removes_existing_backup(self, tmp_path):
        current = tmp_path / "venv"
        current.mkdir()
        (current / "marker").write_text("v2")
        new = tmp_path / "venv.new"
        new.mkdir()
        (new / "marker").write_text("v3")
        backup = tmp_path / "venv.old"
        backup.mkdir()
        (backup / "marker").write_text("stale")
        _v.atomic_swap(current, new, backup)
        # New backup is v2 (the one that was current), not the stale "v1" file.
        assert (backup / "marker").read_text() == "v2"


class TestRollback:
    def test_rollback_restores_backup(self, tmp_path):
        current = tmp_path / "venv"
        current.mkdir()
        (current / "marker").write_text("v3")
        backup = tmp_path / "venv.old"
        backup.mkdir()
        (backup / "marker").write_text("v2")
        broken = tmp_path / "venv.bad"
        _v.rollback(current, backup, broken)
        assert (current / "marker").read_text() == "v2"
        assert (broken / "marker").read_text() == "v3"

    def test_rollback_without_backup_raises(self, tmp_path):
        current = tmp_path / "venv"
        current.mkdir()
        with pytest.raises(_v.VenvOpError, match="no rollback target"):
            _v.rollback(current, tmp_path / "missing", tmp_path / "broken")


class TestWriteWrapperMarker:
    def test_marker_dropped_in_venv(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        _v.write_wrapper_marker(venv)
        marker = venv / ".kt-wrapper-marker"
        assert marker.is_file()
        assert "kt-wrapper" in marker.read_text()
