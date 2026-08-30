import json
import ctypes
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.serving import desktop

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"


class _Events:
    def __init__(self):
        self.shown = _Event()
        self.closed = _Event()


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class _ImmediateThread:
    def __init__(self, *, target, kwargs, **_ignored):
        self.target = target
        self.kwargs = kwargs

    def start(self):
        self.target(**self.kwargs)


class _Window:
    def __init__(self):
        self.events = _Events()
        self.loaded = []
        self.html = []
        self.exposed = []

    def expose(self, *functions):
        self.exposed.extend(functions)

    def load_url(self, url):
        self.loaded.append(url)

    def load_html(self, html):
        self.html.append(html)


class _Webview:
    def __init__(self, messages):
        self.messages = messages
        self.window = _Window()
        self.created = []

    def create_window(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return self.window

    def start(self, func=None, args=None, **_kwargs):
        if func:
            func(*(args or ()))
        self.window.events.shown.fire()
        self.window.events.closed.fire()


def test_desktop_module_import_stays_lightweight():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; import kohakuterrarium.serving.desktop; "
            "print(json.dumps(sorted(sys.modules)))",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(json.loads(result.stdout))

    assert "kohakuterrarium.serving.web" not in loaded
    assert "kohakuterrarium.api.app" not in loaded
    assert not any(name.startswith("fastapi") for name in loaded)
    assert not any(name.startswith("uvicorn") for name in loaded)


def test_launcher_detaches_lightweight_desktop_process(monkeypatch, tmp_path):
    calls = []
    child = SimpleNamespace(pid=73)
    monkeypatch.setattr(desktop, "_is_briefcase_runtime", lambda: False)
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda cmd, **kwargs: calls.append((cmd, kwargs)) or child,
    )
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))

    result = desktop.launch_desktop_app(port=8123, log_level="ERROR")

    assert result is child
    assert (tmp_path / "config" / "app.log").exists()
    assert calls[0][0][:3] == [sys.executable, "-m", "kohakuterrarium.serving.desktop"]
    assert "8123" in calls[0][0]


def test_subprocess_server_log_honors_config_dir(monkeypatch, tmp_path):
    calls = []
    child = SimpleNamespace(pid=74)
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda cmd, **kwargs: calls.append((cmd, kwargs)) or child,
    )

    result = desktop._start_subprocess_server(
        port=8123,
        log_level="ERROR",
        state_path=tmp_path / "state.json",
    )

    assert result is child
    assert (tmp_path / "config" / "app.log").exists()


def test_briefcase_uses_in_process_server(monkeypatch):
    in_process = object()
    monkeypatch.setattr(desktop, "_is_briefcase_runtime", lambda: True)
    monkeypatch.setattr(
        desktop, "_start_in_process_server", lambda **_kwargs: in_process
    )
    monkeypatch.setattr(
        desktop,
        "_start_subprocess_server",
        lambda **_kwargs: pytest.fail("subprocess used"),
    )

    assert (
        desktop._start_server(port=8001, log_level="ERROR", state_path=Path("state"))
        is in_process
    )


def test_windows_desktop_restores_native_app_and_window_icons(monkeypatch):
    calls = []
    shell32 = SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda app_id: calls.append(
            ("app-id", app_id)
        )
    )
    user32 = SimpleNamespace(
        LoadImageW=lambda *_args: 73,
        FindWindowW=lambda *_args: 91,
        SendMessageW=lambda *args: calls.append(("window-icon", *args)),
    )
    webview = _Webview([])
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=shell32, user32=user32),
        raising=False,
    )
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    monkeypatch.setattr(
        desktop, "_start_server", lambda **_kwargs: SimpleNamespace(pid=42)
    )
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "ready", "port": 8123},
    )
    monkeypatch.setattr(desktop, "_stop_server_child", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(desktop.Path, "exists", lambda _self: True)

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert ("app-id", "KohakuLab.KohakuTerrarium") in calls
    assert ("window-icon", 91, 0x0080, 0, 73) in calls
    assert ("window-icon", 91, 0x0080, 1, 73) in calls


def test_macos_desktop_restores_native_dock_icon(monkeypatch):
    applied = []
    webview = _Webview([])
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    monkeypatch.setattr(
        desktop, "_start_server", lambda **_kwargs: SimpleNamespace(pid=42)
    )
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "ready", "port": 8123},
    )
    monkeypatch.setattr(desktop, "_stop_server_child", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        desktop, "_set_icon_macos", lambda _path: applied.append("dock-icon")
    )

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert applied == ["dock-icon"]


def test_server_start_overlaps_loading_window_startup(monkeypatch):
    order = []
    messages = [{"status": "ready", "port": 8123}]
    webview = _Webview(messages)
    webview.window.events.shown += lambda: order.append("shown")

    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    monkeypatch.setattr(
        desktop,
        "_start_server",
        lambda **_kwargs: order.append("server") or SimpleNamespace(pid=42),
    )
    monkeypatch.setattr(
        desktop, "_wait_for_server", lambda *_args, **_kwargs: messages[0]
    )
    monkeypatch.setattr(desktop, "_stop_server_child", lambda *_args, **_kwargs: None)

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert [function.__name__ for function in webview.window.exposed] == [
        "get_desktop_capabilities",
        "request_desktop_attention",
    ]
    assert order[:2] == ["server", "shown"]
    assert webview.window.loaded == ["http://127.0.0.1:8123"]
    assert webview.created[0][1]["html"] == desktop.LOADING_HTML


def test_server_fallback_port_drives_navigation(monkeypatch):
    webview = _Webview([])
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    monkeypatch.setattr(
        desktop, "_start_server", lambda **_kwargs: SimpleNamespace(pid=42)
    )
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "ready", "port": 8002},
    )
    monkeypatch.setattr(desktop, "_stop_server_child", lambda *_args, **_kwargs: None)

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert webview.window.loaded == ["http://127.0.0.1:8002"]


def test_server_error_replaces_loading_shell(monkeypatch):
    webview = _Webview([])
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    monkeypatch.setattr(
        desktop, "_start_server", lambda **_kwargs: SimpleNamespace(pid=42)
    )
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "error", "error": "bind failed"},
    )
    monkeypatch.setattr(desktop, "_stop_server_child", lambda *_args, **_kwargs: None)

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert not webview.window.loaded
    assert "bind failed" in webview.window.html[-1]


def test_close_during_start_stops_late_server(monkeypatch):
    stopped = []
    webview = _Webview([])
    child = SimpleNamespace(pid=42)
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)

    def start_then_close(**_kwargs):
        webview.window.events.closed.fire()
        return child

    monkeypatch.setattr(desktop, "_start_server", start_then_close)
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "ready", "port": 8123},
    )
    monkeypatch.setattr(
        desktop, "_stop_server_child", lambda value, *_args: stopped.append(value)
    )

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert stopped
    assert all(value is child for value in stopped)
    assert not webview.window.loaded


def test_window_close_stops_server_child(monkeypatch):
    stopped = []
    webview = _Webview([])
    monkeypatch.setattr(desktop, "configure_utf8_stdio", lambda **_kwargs: None)
    monkeypatch.setattr(desktop.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(desktop, "_load_webview", lambda: webview)
    child = SimpleNamespace(pid=42)
    monkeypatch.setattr(desktop, "_start_server", lambda **_kwargs: child)
    monkeypatch.setattr(
        desktop,
        "_wait_for_server",
        lambda *_args, **_kwargs: {"status": "ready", "port": 8123},
    )
    monkeypatch.setattr(
        desktop, "_stop_server_child", lambda value, *_args: stopped.append(value)
    )

    desktop.run_desktop_app(port=8001, log_level="ERROR")

    assert stopped == [child]


def test_server_child_publishes_ready_state(monkeypatch, tmp_path):
    from kohakuterrarium.serving import desktop_server

    state = tmp_path / "state.json"
    server = SimpleNamespace(should_exit=True)
    monkeypatch.setattr(
        desktop_server,
        "start_desktop_server",
        lambda _port, _level: (server, 8002),
    )

    assert desktop_server.run_server_child(8001, "ERROR", state) == 0
    assert json.loads(state.read_text()) == {"status": "ready", "port": 8002}


def test_server_child_publishes_startup_error(monkeypatch, tmp_path):
    from kohakuterrarium.serving import desktop_server

    state = tmp_path / "state.json"
    monkeypatch.setattr(
        desktop_server,
        "start_desktop_server",
        lambda _port, _level: (_ for _ in ()).throw(RuntimeError("bind failed")),
    )

    assert desktop_server.run_server_child(8001, "ERROR", state) == 1
    assert json.loads(state.read_text()) == {
        "status": "error",
        "error": "bind failed",
    }


def test_in_process_server_waits_for_uvicorn_thread():
    joined = []
    thread = SimpleNamespace(join=lambda timeout: joined.append(timeout))
    server = SimpleNamespace(should_exit=False, _kt_thread=thread)

    child = desktop._InProcessServer(server)
    child.terminate()
    child.wait(timeout=3)

    assert server.should_exit is True
    assert joined == [3]


def test_server_child_reports_runtime_exit(monkeypatch, tmp_path):
    from kohakuterrarium.serving import desktop_server

    state = tmp_path / "state.json"
    thread = SimpleNamespace(is_alive=lambda: False)
    server = SimpleNamespace(should_exit=False, _kt_thread=thread)
    monkeypatch.setattr(
        desktop_server,
        "start_desktop_server",
        lambda _port, _level: (server, 8002),
    )

    assert desktop_server.run_server_child(8001, "ERROR", state) == 1
    assert json.loads(state.read_text()) == {
        "status": "error",
        "error": "Desktop server stopped unexpectedly.",
    }


def test_windows_pid_probe_uses_process_handle(monkeypatch):
    from kohakuterrarium.serving import desktop_server

    calls = []
    kernel32 = SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: calls.append((access, inherit, pid))
        or 99,
        WaitForSingleObject=lambda handle, timeout: 0x00000102,
        CloseHandle=lambda handle: calls.append(("close", handle)),
    )
    monkeypatch.setattr(desktop_server.sys, "platform", "win32")
    monkeypatch.setattr(
        desktop_server.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    assert desktop_server._pid_alive(42) is True
    assert calls[-1] == ("close", 99)


def test_server_child_exits_when_ui_parent_dies(monkeypatch, tmp_path):
    from kohakuterrarium.serving import desktop_server

    state = tmp_path / "state.json"
    joined = []
    thread = SimpleNamespace(
        is_alive=lambda: True, join=lambda timeout: joined.append(timeout)
    )
    server = SimpleNamespace(should_exit=False, _kt_thread=thread)
    monkeypatch.setattr(
        desktop_server,
        "start_desktop_server",
        lambda _port, _level: (server, 8002),
    )
    monkeypatch.setattr(desktop_server, "_pid_alive", lambda _pid: False)

    assert desktop_server.run_server_child(8001, "ERROR", state, parent_pid=42) == 0
    assert server.should_exit is True
    assert joined == [5]


def test_server_child_honors_shutdown_state(monkeypatch, tmp_path):
    from kohakuterrarium.serving import desktop_server

    state = tmp_path / "state.json"
    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(
        desktop_server,
        "start_desktop_server",
        lambda _port, _level: (server, 8002),
    )

    def request_shutdown(_seconds):
        state.write_text(json.dumps({"status": "shutdown"}))

    monkeypatch.setattr(desktop_server.time, "sleep", request_shutdown)

    assert desktop_server.run_server_child(8001, "ERROR", state) == 0
    assert server.should_exit is True
