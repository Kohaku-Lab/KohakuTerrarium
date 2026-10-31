"""runtime_info / repair: what the shell shows about the release trees and how it repairs them."""

import json

import pytest

from kohakuterrarium.launcher import paths as _p
from kohakuterrarium.launcher import repair as _repair
from kohakuterrarium.launcher import runtime_info as _info
from kohakuterrarium.launcher import tree_ops as _t
from kohakuterrarium.launcher import update_runner as _r


@pytest.fixture
def cfg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [])
    return tmp_path


def _install(version: str, *, active: bool = False) -> None:
    root = _p.version_dir(version)
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"version": version, "build_id": f"b-{version}"}), encoding="utf-8"
    )
    if active:
        _t.write_active_pointer(version, build_id=f"b-{version}")


def _ship(tmp_path, monkeypatch, version: str) -> None:
    bundled = tmp_path / "bundled-release"
    bundled.mkdir()
    (bundled / f"kohakuterrarium-{version}-win-x64-py3.13.tar.zst").write_bytes(b"x")
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [bundled])


def test_an_empty_runtime_has_nothing_active_installed_or_shipped(cfg_home):
    info = _info.describe()
    assert info["active"] is None
    assert info["installed"] == []
    assert info["bundled"] is None
    assert info["bundled_newer"] is False
    assert info["runtime_dir"] == str(_p.runtime_dir())


def test_the_active_release_is_marked_among_the_installed_ones(cfg_home):
    _install("2.0.0.dev1")
    _install("2.2.1.dev11", active=True)
    info = _info.describe()
    assert info["active"]["version"] == "2.2.1.dev11"
    assert info["active"]["build_id"] == "b-2.2.1.dev11"
    flags = {item["version"]: item["active"] for item in info["installed"]}
    assert flags == {"2.0.0.dev1": False, "2.2.1.dev11": True}


def test_a_shipped_release_newer_than_the_active_one_is_flagged(
    cfg_home, monkeypatch, tmp_path
):
    _install("2.0.0.dev5+abc", active=True)
    _ship(tmp_path, monkeypatch, "2.2.1.dev11")
    info = _info.describe()
    assert info["bundled"] == "2.2.1.dev11"
    assert info["bundled_newer"] is True


def test_a_shipped_release_that_is_current_or_older_is_not_flagged(
    cfg_home, monkeypatch, tmp_path
):
    _install("2.2.1.dev11", active=True)
    _ship(tmp_path, monkeypatch, "2.2.1.dev11")
    assert _info.describe()["bundled_newer"] is False
    _ship_older = tmp_path / "older"
    _ship_older.mkdir()
    (_ship_older / "kohakuterrarium-2.0.0-win-x64-py3.13.tar.zst").write_bytes(b"x")
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [_ship_older])
    assert _info.describe()["bundled_newer"] is False


def test_a_shipped_release_without_any_active_one_counts_as_newer(
    cfg_home, monkeypatch, tmp_path
):
    _ship(tmp_path, monkeypatch, "2.2.1.dev11")
    assert _info.describe()["bundled_newer"] is True


def test_repair_reports_progress_then_a_result_line_and_the_exit_code(
    monkeypatch, capsys
):
    def fake_reset(progress):
        progress("extract", 20.0, "Unpacking")
        return _r.UpdateResult(ok=True, version="2.2.1.dev11")

    monkeypatch.setattr(_repair, "reset", fake_reset)
    assert _repair.main() == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {"phase": "extract", "percent": 20.0, "message": "Unpacking"}
    assert lines[-1] == {"ok": True, "version": "2.2.1.dev11", "error": None}


def test_repair_fails_with_the_reason_when_nothing_could_be_installed(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        _repair, "reset", lambda progress: _r.UpdateResult(ok=False, error="no bundle")
    )
    assert _repair.main() == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {
        "ok": False,
        "version": None,
        "error": "no bundle",
    }


def test_repair_really_replaces_a_stale_runtime_with_the_shipped_release(
    cfg_home, monkeypatch, tmp_path, capsys
):
    _install("2.0.0.dev5+abc", active=True)
    bundled = tmp_path / "bundled-release"
    bundled.mkdir()
    import io
    import tarfile

    tarball = bundled / "kohakuterrarium-2.2.1.dev11-win-x64-py3.13.tar.gz"
    with tarfile.open(str(tarball), mode="w:gz") as tar:
        for name, data in {
            "manifest.json": json.dumps({"version": "2.2.1.dev11"}).encode(),
            "site-packages/kohakuterrarium/__init__.py": b'__version__ = "2.2.1.dev11"\n',
        }.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(_p, "_candidate_bundled_release_dirs", lambda: [bundled])
    monkeypatch.setattr(_r, "smoke_test_tree", lambda root: "2.2.1.dev11")

    assert _repair.main() == 0
    capsys.readouterr()
    assert _t.read_active_pointer().version == "2.2.1.dev11"
    assert not _p.version_dir("2.0.0.dev5+abc").exists()
    assert _p.version_dir("2.2.1.dev11").is_dir()
