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
CAPABILITY_FILES = ("main.json", "overlay.json", "popout.json")


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


def _allowed(capability_file: str) -> set[str]:
    capability = json.loads(_text(TAURI / "capabilities" / capability_file))
    return {
        item.removeprefix("allow-").replace("-", "_")
        for item in capability["permissions"]
        if item.startswith("allow-")
    }


def test_every_command_is_declared_allowed_and_registered():
    declared = set()
    for module in (TAURI / "src").rglob("*.rs"):
        declared |= set(
            re.findall(
                r"#\[tauri::command\]\s*pub (?:async )?fn ([a-z_]+)\(",
                _text(module),
            )
        )
    manifest = set(re.findall(r'"([a-z_]+)",', _text(TAURI / "build.rs")))
    handler = _text(TAURI / "src" / "lib.rs").split("generate_handler![")[1]
    registered = set(
        re.findall(r"[a-z_]+::([a-z_]+),", handler.split("])", 1)[0]),
    )
    assert declared == manifest == registered
    allowed = set()
    for name in CAPABILITY_FILES:
        allowed |= _allowed(name)
    assert declared == allowed


def test_secondary_windows_may_only_call_commands_that_act_on_themselves():
    powerful = {
        "open_overlay_window",
        "open_popout_window",
        "close_overlay_window",
        "install_shell_update",
        "set_autostart",
        "show_notification",
        "set_summon_shortcut",
        "set_close_to_tray",
        "take_pending_opens",
    }
    overlay = _allowed("overlay.json")
    assert {"overlay_start_drag", "overlay_set_bounds", "overlay_ack"} <= overlay
    assert not overlay & (powerful - {"close_overlay_window"})
    popout = _allowed("popout.json")
    assert "popout_ack" in popout
    assert not popout & powerful


def test_app_page_origin_is_the_only_remote_capability():
    windows_by_file = {
        "main.json": ["main"],
        "overlay.json": ["overlay-*"],
        "popout.json": ["popout-*"],
    }
    for name, windows in windows_by_file.items():
        capability = json.loads(_text(TAURI / "capabilities" / name))
        assert capability["remote"]["urls"] == ["http://127.0.0.1:*"]
        assert capability["windows"] == windows


def test_tray_capabilities_the_page_checks_are_declared_by_the_shell():
    commands_rs = _text(TAURI / "src" / "commands.rs")
    effects = _text(
        ROOT
        / "src"
        / "kohakuterrarium-frontend"
        / "src"
        / "composables"
        / "useAttentionEffects.js"
    )
    for flag in re.findall(r"hostCapabilities\?\.([A-Za-z]+) !== true", effects):
        assert f'"{flag}": true' in commands_rs, flag


def test_session_file_association_and_open_event_match_the_app():
    conf = json.loads(_text(TAURI / "tauri.conf.json"))
    extensions = {
        ext for item in conf["bundle"]["fileAssociations"] for ext in item["ext"]
    }
    assert extensions == {"kohakutr"}
    opens_rs = _text(TAURI / "src" / "opens.rs")
    assert _rust_const(opens_rs, "SESSION_EXTENSION") in extensions
    event = _rust_const(opens_rs, "OPEN_EVENT")
    composable = _text(
        ROOT
        / "src"
        / "kohakuterrarium-frontend"
        / "src"
        / "composables"
        / "useDesktopOpen.js"
    )
    assert f'OPEN_EVENT = "{event}"' in composable
    for kind in ("session", "workspace"):
        assert f'"{kind}"' in opens_rs
        assert f'"{kind}"' in composable
    route = _text(SRC / "api" / "routes" / "app_open.py")
    assert '"/open-session"' in route
    assert "/app/open-session" in _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "utils" / "appOpenApi.js"
    )


def test_the_shell_record_kt_open_reads_is_the_file_the_shell_writes():
    from kohakuterrarium.cli import open_in_app

    runtime_rs = _text(TAURI / "src" / "runtime" / "mod.rs")
    assert _rust_const(runtime_rs, "SHELL_RECORD") == open_in_app.SHELL_RECORD
    assert 'json!({ "exe": exe })' in runtime_rs
    assert open_in_app.SESSION_SUFFIX == "." + _rust_const(
        _text(TAURI / "src" / "opens.rs"), "SESSION_EXTENSION"
    )


def test_the_uninstaller_removes_every_explorer_entry_the_app_can_add():
    conf = json.loads(_text(TAURI / "tauri.conf.json"))
    hooks = _text(TAURI / conf["bundle"]["windows"]["nsis"]["installerHooks"])
    explorer_rs = _text(TAURI / "src" / "os" / "explorer.rs")
    parents = re.findall(r'\(r"([^"]+)", "%[1V]"\)', explorer_rs)
    assert len(parents) == 2
    verb = _rust_const(explorer_rs, "VERB")
    for parent in parents:
        assert f'DeleteRegKey HKCU "Software\\Classes\\{parent}\\{verb}"' in hooks
    assert "!macro NSIS_HOOK_POSTUNINSTALL" in hooks


def test_file_drop_event_name_matches_the_frontend():
    files_rs = _text(TAURI / "src" / "files.rs")
    event = _rust_const(files_rs, "DROP_EVENT")
    composable = _text(
        ROOT
        / "src"
        / "kohakuterrarium-frontend"
        / "src"
        / "composables"
        / "useDesktopFileDrop.js"
    )
    assert f'FILE_DROP_EVENT = "{event}"' in composable
    for phase in ("enter", "over", "drop", "leave"):
        assert f'"phase": "{phase}"' in files_rs
    for phase in ("drop", "leave"):
        assert f'phase === "{phase}"' in composable


def test_menu_actions_are_all_handled_by_the_page():
    menu_rs = _text(TAURI / "src" / "menu.rs")
    event = _rust_const(menu_rs, "MENU_EVENT")
    composable = _text(
        ROOT
        / "src"
        / "kohakuterrarium-frontend"
        / "src"
        / "composables"
        / "useDesktopMenu.js"
    )
    assert f'MENU_EVENT = "{event}"' in composable
    declared = re.findall(r'^\s*\("([a-z-]+)", "[^"]+", ', menu_rs, re.MULTILINE)
    assert declared
    for action in declared:
        assert re.search(rf'(?:^|\s)"?{re.escape(action)}"?:', composable), action


def test_tray_item_event_name_matches_the_frontend():
    tray_rs = _text(TAURI / "src" / "tray.rs")
    event = _rust_const(tray_rs, "TRAY_ITEM_EVENT")
    tray_items = _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "ext" / "trayItems.js"
    )
    assert f'TRAY_ITEM_EVENT = "{event}"' in tray_items
    bridge = _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "utils" / "hostBridge.js"
    )
    assert '"set_tray_items"' in bridge
    assert "set_tray_items" in _allowed("main.json")


def test_popout_page_parameter_and_window_prefix_match_the_frontend():
    popout_rs = _text(TAURI / "src" / "popout.rs")
    popout_js = _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "utils" / "popout.js"
    )
    param = _rust_const(popout_rs, "POPOUT_PARAM")
    prefix = _rust_const(popout_rs, "LABEL_PREFIX")
    assert f'POPOUT_PARAM = "{param}"' in popout_js
    assert f'POPOUT_LABEL_PREFIX = "{prefix}"' in popout_js
    assert prefix != _rust_const(_text(TAURI / "src" / "overlay.rs"), "LABEL_PREFIX")


def test_overlay_page_parameter_and_window_prefix_match_the_frontend():
    overlay_rs = _text(TAURI / "src" / "overlay.rs")
    param = _rust_const(overlay_rs, "OVERLAY_PARAM")
    prefix = _rust_const(overlay_rs, "LABEL_PREFIX")
    windows = _text(
        ROOT / "src" / "kohakuterrarium-frontend" / "src" / "ext" / "overlayWindows.js"
    )
    assert f'OVERLAY_PARAM = "{param}"' in windows
    assert f'WINDOW_LABEL_PREFIX = "{prefix}"' in windows


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
    shell = set()
    for module in (TAURI / "src").rglob("*.rs"):
        shell |= set(re.findall(r"pub (?:async )?fn ([a-z_]+)\(", _text(module)))
    assert called and called <= shell
