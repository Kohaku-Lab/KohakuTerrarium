"""The Rust shell and the Python/JS code share names that nothing else checks.

Each case reads both sides and compares them, so renaming one side fails here
instead of at install time.
"""

import json
import re
from pathlib import Path

import pytest

from kohakuterrarium.utils import desktop_launch
from kohakuterrarium.utils.interpreter import PYTHON_ENV

ROOT = Path(__file__).resolve().parents[3]
TAURI = ROOT / "packaging" / "desktop" / "src-tauri"
SRC = ROOT / "src" / "kohakuterrarium"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def backend_rs() -> str:
    return _text(TAURI / "src" / "backend.rs")


def _rust_const(source: str, name: str) -> str:
    match = re.search(rf'const {name}: &str = "([^"]+)";', source)
    assert match, f"{name} not found"
    return match.group(1)


def test_launch_token_names_match_the_python_server(backend_rs):
    assert _rust_const(backend_rs, "LAUNCH_ENV") == desktop_launch.LAUNCH_ENV
    assert _rust_const(backend_rs, "QUERY_PARAM") == desktop_launch.QUERY_PARAM


def test_launch_url_has_the_shape_the_python_helper_builds(backend_rs):
    assert 'format!("http://127.0.0.1:{port}/?{QUERY_PARAM}={token}")' in backend_rs
    assert desktop_launch.launch_url(8001, "T") == "http://127.0.0.1:8001/?kt_launch=T"


def test_sidecar_module_the_shell_runs_exists(backend_rs):
    module = re.search(r'"(kohakuterrarium\.launcher\.[a-z_]+)"\.into\(\)', backend_rs)
    assert module
    assert (SRC / Path(*module.group(1).split(".")[1:]).with_suffix(".py")).is_file()


def test_environment_the_shell_sets_is_read_by_python(backend_rs):
    for name in ("KT_PYTHON", "KT_BUNDLED_RELEASE_DIR", "KT_BOOTSTRAP_DIR"):
        assert f'"{name}"' in backend_rs
    assert PYTHON_ENV == "KT_PYTHON"
    assert "KT_BUNDLED_RELEASE_DIR" in _text(SRC / "launcher" / "paths.py")


def test_resource_directories_match_the_layout_and_the_config(backend_rs):
    conf = json.loads(_text(TAURI / "tauri.conf.json"))
    resources = conf["bundle"]["resources"]
    assert set(resources.values()) == {"python", "bootstrap", "bundled-release"}
    for source in resources:
        assert (TAURI / source).is_dir(), f"{source} must exist for the build"
    for name in resources.values():
        assert f'"{name}"' in backend_rs


def test_every_command_is_declared_allowed_and_registered():
    commands_rs = _text(TAURI / "src" / "commands.rs")
    declared = set(re.findall(r"pub fn ([a-z_]+)\(", commands_rs)) & set(
        re.findall(r"#\[tauri::command\]\s*pub fn ([a-z_]+)\(", commands_rs)
    )
    manifest = set(re.findall(r'"([a-z_]+)",', _text(TAURI / "build.rs")))
    registered = set(
        re.findall(r"commands::([a-z_]+)", _text(TAURI / "src" / "lib.rs"))
    )
    capability = json.loads(_text(TAURI / "capabilities" / "main.json"))
    allowed = {
        item.removeprefix("allow-").replace("-", "_")
        for item in capability["permissions"]
        if item.startswith("allow-")
    }
    assert declared == manifest == registered == allowed


def test_app_page_origin_is_the_only_remote_capability():
    capability = json.loads(_text(TAURI / "capabilities" / "main.json"))
    assert capability["remote"]["urls"] == ["http://127.0.0.1:*"]
    assert capability["windows"] == ["main"]


def test_protocol_version_agrees_across_shells_and_frontend():
    rust = re.search(r"PROTOCOL: u32 = (\d+);", _text(TAURI / "src" / "commands.rs"))
    js = re.search(
        r"DESKTOP_PROTOCOL = (\d+)",
        _text(
            ROOT
            / "src"
            / "kohakuterrarium-frontend"
            / "src"
            / "utils"
            / "hostBridge.js"
        ),
    )
    python = re.search(
        r'"protocol": (\d+)', _text(SRC / "serving" / "desktop" / "attention.py")
    )
    assert rust and js and python
    assert rust.group(1) == js.group(1) == python.group(1)


def test_commands_the_frontend_calls_exist_in_the_shell():
    bridge = _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "utils" / "hostBridge.js"
    )
    called = set(re.findall(r'(?:hostSupports|call)\("([a-z_]+)"', bridge))
    shell = set(re.findall(r"pub fn ([a-z_]+)\(", _text(TAURI / "src" / "commands.rs")))
    assert called and called <= shell
