"""Lightweight native desktop bootstrap with a loading shell."""

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from kohakuterrarium.utils.logging import configure_utf8_stdio
from kohakuterrarium.utils.startup_trace import mark as mark_startup

LOADING_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#151525;color:#e8e8f2;font:16px system-ui,sans-serif}
body{display:grid;place-items:center}.shell{text-align:center}.spinner{width:34px;height:34px;
margin:0 auto 18px;border:3px solid #373751;border-top-color:#9b87f5;border-radius:50%;
animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body><div class="shell"><div class="spinner"></div>
<div>Starting KohakuTerrarium…</div></div></body></html>"""


class _InProcessServer:
    def __init__(self, server):
        self.server = server
        self.pid = os.getpid()

    def poll(self):
        return None if not self.server.should_exit else 0

    def terminate(self):
        self.server.should_exit = True

    def wait(self, timeout=None):
        self.server.should_exit = True
        thread = getattr(self.server, "_kt_thread", None)
        if thread is not None:
            thread.join(timeout)
        return 0

    def kill(self):
        self.server.should_exit = True


def _error_html(message: str) -> str:
    escaped = html.escape(message)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{height:100%;margin:0;background:#151525;color:#e8e8f2;font:16px system-ui,sans-serif}}
body{{display:grid;place-items:center}}.shell{{max-width:640px;padding:32px}}h2{{color:#ff8f8f}}
pre{{white-space:pre-wrap;color:#cfcfe5}}</style></head><body><div class="shell">
<h2>Startup failed</h2><pre>{escaped}</pre></div></body></html>"""


def _is_briefcase_runtime() -> bool:
    if os.environ.get("KT_LAUNCHER_EXEC") == "1":
        return True
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return False
    return any(exe_dir.glob("python3*._pth"))


def _load_webview():
    try:
        import webview
    except ImportError:
        print("pywebview is required for 'kt app'.")
        print("Install: pip install 'KohakuTerrarium[desktop]'")
        raise SystemExit(1) from None
    return webview


def _set_windows_app_id() -> None:
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "KohakuLab.KohakuTerrarium"
        )
    except Exception:
        pass


def _set_icon_windows(icon_path: Path) -> None:
    try:
        import ctypes

        if not icon_path.exists():
            return
        user32 = ctypes.windll.user32
        hicon = user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x00000010)
        if not hicon:
            return
        hwnd = user32.FindWindowW(None, "KohakuTerrarium")
        if hwnd:
            user32.SendMessageW(hwnd, 0x0080, 0, hicon)
            user32.SendMessageW(hwnd, 0x0080, 1, hicon)
    except Exception:
        pass


def _set_icon_macos(icon_path: Path) -> None:
    try:
        if not icon_path.exists():
            return
        from AppKit import NSApp, NSApplication, NSImage
        from PyObjCTools import AppHelper

        def apply_icon():
            app = NSApp() or NSApplication.sharedApplication()
            image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if image:
                app.setApplicationIconImage_(image)

        AppHelper.callAfter(apply_icon)
    except Exception:
        pass


def _state_path() -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="kt-desktop-", suffix=".json", delete=False
    )
    handle.close()
    return Path(handle.name)


def launch_desktop_app(port: int = 8001, log_level: str = "INFO"):
    """Detach a lightweight desktop UI process from a regular interpreter."""
    if _is_briefcase_runtime():
        run_desktop_app(port=port, log_level=log_level)
        return None
    cmd = [
        sys.executable,
        "-m",
        "kohakuterrarium.serving.desktop",
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]
    log_dir = Path.home() / ".kohakuterrarium"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "app.log", "w", encoding="utf-8")  # noqa: SIM115
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(cmd, **kwargs)
    log_file.close()
    mark_startup(
        "desktop_child_spawned",
        surface="desktop",
        child_pid=child.pid,
        port=port,
    )
    return child


def _start_subprocess_server(*, port: int, log_level: str, state_path: Path):
    cmd = [
        sys.executable,
        "-m",
        "kohakuterrarium.serving.desktop_server",
        "--port",
        str(port),
        "--log-level",
        log_level,
        "--state-path",
        str(state_path),
        "--parent-pid",
        str(os.getpid()),
    ]
    log_file = open(  # noqa: SIM115
        Path.home() / ".kohakuterrarium" / "app.log", "a", encoding="utf-8"
    )
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(cmd, **kwargs)
    log_file.close()
    mark_startup(
        "desktop_server_child_spawned",
        surface="desktop",
        child_pid=child.pid,
        port=port,
    )
    return child


def _start_in_process_server(*, port: int, log_level: str, state_path: Path):
    from kohakuterrarium.serving.desktop_server import start_desktop_server

    server, actual_port = start_desktop_server(port, log_level)
    state_path.write_text(
        json.dumps({"status": "ready", "port": actual_port}), encoding="utf-8"
    )
    return _InProcessServer(server)


def _start_server(*, port: int, log_level: str, state_path: Path):
    if _is_briefcase_runtime():
        return _start_in_process_server(
            port=port, log_level=log_level, state_path=state_path
        )
    return _start_subprocess_server(
        port=port, log_level=log_level, state_path=state_path
    )


def _wait_for_server(child, state_path: Path, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if data.get("status") in {"ready", "error"}:
                return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        if child.poll() is not None:
            return {"status": "error", "error": "Desktop server exited during startup."}
        time.sleep(0.05)
    return {"status": "error", "error": "Desktop server did not start in time."}


def _stop_server_child(child, state_path: Path | None = None) -> None:
    if child.poll() is not None:
        return
    if state_path is not None:
        try:
            state_path.write_text(json.dumps({"status": "shutdown"}), encoding="utf-8")
            child.wait(timeout=5)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _run_startup(
    window,
    *,
    port: int,
    log_level: str,
    state_path: Path,
    child_ref: list,
    closed: threading.Event,
) -> None:
    try:
        if closed.is_set():
            return
        child = _start_server(port=port, log_level=log_level, state_path=state_path)
        child_ref.append(child)
        if closed.is_set():
            _stop_server_child(child, state_path)
            return
        result = _wait_for_server(child, state_path)
        if closed.is_set():
            _stop_server_child(child, state_path)
            return
        if result.get("status") == "ready":
            actual_port = int(result["port"])
            mark_startup("desktop_server_ready", surface="desktop", port=actual_port)
            window.load_url(f"http://127.0.0.1:{actual_port}")
            return
        error = str(result.get("error") or "Unknown desktop startup error")
    except Exception as exc:
        error = str(exc)
    mark_startup("desktop_startup_failed", surface="desktop", error=error)
    window.load_html(_error_html(error))


def run_desktop_app(port: int = 8001, log_level: str = "INFO") -> None:
    """Show a native loading shell, then start and navigate to the local server."""
    configure_utf8_stdio(log=True)
    os.environ["KT_STARTUP_SURFACE"] = "desktop"
    webview = _load_webview()
    icons_dir = Path(__file__).parent.parent / "app_icons"
    icon_ico = icons_dir / "window.ico"
    icon_png = icons_dir / "window.png"
    if sys.platform == "win32":
        _set_windows_app_id()
    state_path = _state_path()
    child_ref: list = []
    closed = threading.Event()
    window = webview.create_window(
        "KohakuTerrarium",
        html=LOADING_HTML,
        width=1280,
        height=800,
        min_size=(800, 500),
        zoomable=True,
        text_select=True,
        confirm_close=True,
        background_color="#1a1a2e",
    )
    from kohakuterrarium.serving.desktop_attention import expose_desktop_attention

    expose_desktop_attention(window)
    mark_startup("desktop_window_created", surface="desktop", window="loading")

    def _shown():
        mark_startup("desktop_window_shown", surface="desktop", window="loading")
        if sys.platform == "win32":
            _set_icon_windows(icon_ico)
        elif sys.platform == "darwin":
            _set_icon_macos(icon_png)

    window.events.shown += _shown

    def _closed():
        closed.set()
        if child_ref:
            _stop_server_child(child_ref[0], state_path)
        try:
            state_path.unlink(missing_ok=True)
        except OSError:
            pass

    window.events.closed += _closed
    threading.Thread(
        target=_run_startup,
        kwargs={
            "window": window,
            "port": port,
            "log_level": log_level,
            "state_path": state_path,
            "child_ref": child_ref,
            "closed": closed,
        },
        daemon=True,
        name="desktop-server-startup",
    ).start()

    if sys.platform == "darwin":
        webview.start(gui="cocoa")
    elif sys.platform == "win32":
        webview.start()
    else:
        webview.start(icon=str(icon_png) if icon_png.exists() else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    args = parser.parse_args()
    run_desktop_app(port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
