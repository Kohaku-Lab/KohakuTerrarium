"""Sidecar: the headless desktop-server entry point used by native shells.

The end-to-end case runs the real module in a child interpreter against a tiny
bundled release archive, exactly as a shell does on first launch.
"""

import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

from kohakuterrarium.launcher import sidecar
from kohakuterrarium.launcher.bootloader import PrepareResult

FAKE_SERVER = """
import json, os, sys
from pathlib import Path

def run_server_child(port, log_level, state_path, parent_pid=0):
    Path(state_path).write_text(json.dumps({
        "status": "ready",
        "port": port,
        "pythonpath": os.environ.get("PYTHONPATH", ""),
        "launcher_exec": os.environ.get("KT_LAUNCHER_EXEC"),
        "module_file": __file__,
        "parent_pid": parent_pid,
    }), encoding="utf-8")
    return 0
"""


def _release_tarball(path, version="9.9.9"):
    members = {
        "manifest.json": json.dumps({"version": version, "build_id": "t"}),
        "site-packages/kohakuterrarium/__init__.py": f'__version__ = "{version}"\n',
        "site-packages/kohakuterrarium/serving/__init__.py": "",
        "site-packages/kohakuterrarium/serving/desktop/__init__.py": "",
        "site-packages/kohakuterrarium/serving/desktop/server.py": FAKE_SERVER,
    }
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_first_launch_installs_reports_progress_and_serves_from_the_release(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _release_tarball(bundled / "kohakuterrarium-9.9.9-win-x64-py3.13.tar.gz")
    state = tmp_path / "state.json"
    env = {
        **os.environ,
        "KT_CONFIG_DIR": str(tmp_path / "cfg"),
        "KT_BUNDLED_RELEASE_DIR": str(bundled),
        "PYTHONPATH": "",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kohakuterrarium.launcher.sidecar",
            "--port",
            "8123",
            "--state-path",
            str(state),
            "--parent-pid",
            "77",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    progress = [json.loads(line) for line in result.stdout.splitlines() if line]
    assert progress and all("phase" in item and "percent" in item for item in progress)
    assert progress[-1]["phase"] == "Starting"
    published = json.loads(state.read_text(encoding="utf-8"))
    site = tmp_path / "cfg" / "runtime" / "versions" / "9.9.9" / "site-packages"
    assert published["status"] == "ready" and published["port"] == 8123
    assert published["parent_pid"] == 77
    assert published["module_file"].startswith(str(site))
    assert published["pythonpath"].split(os.pathsep)[0] == str(site)
    assert published["launcher_exec"] == "1"


def test_unpreparable_runtime_is_reported_through_the_state_file(
    monkeypatch, tmp_path, capsys
):
    state = tmp_path / "state.json"
    monkeypatch.setattr(
        sidecar,
        "prepare",
        lambda *_a, **_k: PrepareResult(exit_code=5, error="offline"),
    )

    code = sidecar.main(["--state-path", str(state)])

    assert code == 5
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "status": "error",
        "error": "offline",
    }


def test_progress_lines_are_single_json_objects(capsys):
    sidecar.emit_progress("Downloading", 42.5, "a.tar.zst")

    line = capsys.readouterr().out
    assert line.count("\n") == 1
    assert json.loads(line) == {
        "phase": "Downloading",
        "percent": 42.5,
        "message": "a.tar.zst",
    }


def test_release_path_is_prepended_for_child_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "boot")
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    monkeypatch.delenv("KT_LAUNCHER_EXEC", raising=False)

    sidecar.export_release_path(tmp_path / "site")

    assert os.environ["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "site"), "boot"])
    assert os.environ["PYTHONNOUSERSITE"] == "1"
    assert os.environ["KT_LAUNCHER_EXEC"] == "1"
    monkeypatch.delenv("PYTHONNOUSERSITE")
    monkeypatch.delenv("KT_LAUNCHER_EXEC")


def test_release_path_without_existing_path_has_no_empty_entry(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHONPATH", raising=False)

    sidecar.export_release_path(tmp_path / "site")

    assert os.environ["PYTHONPATH"] == str(tmp_path / "site")
    monkeypatch.delenv("PYTHONPATH")
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    monkeypatch.delenv("KT_LAUNCHER_EXEC", raising=False)


@pytest.mark.parametrize("missing", ["--state-path"])
def test_state_path_is_required(missing):
    with pytest.raises(SystemExit):
        sidecar.parse_args([])
