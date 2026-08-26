"""Serve the web application or host it in a native desktop window.

Web mode runs FastAPI with the built Vue frontend. Desktop mode adds a
pywebview shell and adapts process handling for regular and Briefcase runtimes.
"""

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from kohakuterrarium.api.app import create_app
from kohakuterrarium.packages.locations import get_package_root, packages_dir
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_file_logging,
    enable_stderr_logging,
    get_logger,
    set_level,
)
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)

# Vite places the packaged frontend beside the Python application modules.
WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web_dist"


def _resolve_config_dirs() -> tuple[list[str], list[str]]:
    """Merge explicit, installed-package, and working-directory config paths.

    Environment paths have highest discovery precedence, followed by installed
    packages and conventional local project directories.
    """
    creatures: list[str] = []
    terrariums: list[str] = []

    # Explicit paths lead the discovery order.
    env_creatures = os.environ.get("KT_CREATURES_DIRS")
    if env_creatures:
        creatures.extend(env_creatures.split(","))
    env_terrariums = os.environ.get("KT_TERRARIUMS_DIRS")
    if env_terrariums:
        terrariums.extend(env_terrariums.split(","))

    if packages_dir().exists():
        for pkg in list_packages():
            pkg_root = get_package_root(pkg["name"])
            if pkg_root:
                c = pkg_root / "creatures"
                t = pkg_root / "terrariums"
                if c.is_dir():
                    creatures.append(str(c))
                if t.is_dir():
                    terrariums.append(str(t))

    # Local directories make project configs visible without installation.
    cwd = Path.cwd()
    for d in (cwd / "creatures", cwd / "agents"):
        if d.is_dir() and str(d) not in creatures:
            creatures.append(str(d))
    cwd_t = cwd / "terrariums"
    if cwd_t.is_dir() and str(cwd_t) not in terrariums:
        terrariums.append(str(cwd_t))

    return creatures, terrariums


def find_free_port(
    start: int = 8001, host: str = "127.0.0.1", max_tries: int = 50
) -> int:
    """Probe sequential TCP ports and return the first bindable candidate.

    The socket is closed before return, so another process may claim the port.
    Use :func:`start_uvicorn_with_port_fallback` when the caller needs a port
    verified by the actual server bind.
    """
    for offset in range(max_tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}-{start + max_tries - 1}")


def start_uvicorn_with_port_fallback(
    app,
    *,
    requested_port: int = 8001,
    host: str = "127.0.0.1",
    max_tries: int = 50,
    log_level: str = "warning",
    startup_timeout: float = 10.0,
):
    """Start uvicorn on the first viable port and return its server and port.

    Each attempt runs in a daemon thread and waits for uvicorn to confirm its
    bind. Dead or timed-out attempts advance to the next port. The returned port
    is read from the live server socket, and callers may request shutdown by
    setting ``server.should_exit``.
    """
    last_exc: Exception | None = None
    for offset in range(max_tries):
        port = requested_port + offset
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
        server = uvicorn.Server(config)
        # Uvicorn runs off the main thread here, where Python forbids signal
        # handler installation.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        thread = threading.Thread(
            target=server.run, daemon=True, name=f"uvicorn-{port}"
        )
        thread.start()

        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            if server.started:
                actual_port = port
                try:
                    sockets = (server.servers or [None])[0].sockets
                    if sockets:
                        actual_port = sockets[0].getsockname()[1]
                except Exception:
                    pass
                return server, actual_port
            if not thread.is_alive():
                # A dead startup thread indicates that this candidate never bound.
                last_exc = RuntimeError(
                    f"uvicorn thread for port {port} died before binding"
                )
                break
            time.sleep(0.05)
        else:
            # Request shutdown before retrying so timed-out threads do not accumulate.
            try:
                server.should_exit = True
            except Exception:
                pass
            last_exc = RuntimeError(
                f"uvicorn for port {port} did not start within {startup_timeout}s"
            )

    raise RuntimeError(
        f"failed to bind any port in [{requested_port}, {requested_port + max_tries})"
    ) from last_exc


def _publish_actual_port(state_path: str | None, host: str, port: int) -> None:
    """Publish the verified server address to an existing daemon state file.

    Direct invocations without a state path and missing state files are ignored.
    """
    if not state_path:
        return
    path = Path(state_path)
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        data["port"] = port
        data["url"] = f"http://{host}:{port}"
        data["bound"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        pass


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8001,
    dev: bool = False,
    log_level: str = "INFO",
    state_path: str | None = None,
    mode: str = "standalone",
    lab_bind: str | None = None,
    lab_token: str | None = None,
) -> None:
    """Run the FastAPI service in standalone or authenticated lab-host mode.

    Development mode omits static frontend serving. Daemon callers may provide
    ``state_path`` to receive the selected port. Lab-host mode also starts the
    WebSocket transport at ``lab_bind`` and requires ``lab_token``.
    """
    configure_utf8_stdio(log=True)

    set_level(log_level)
    # Daemon stderr is the user-facing service log, so mirror framework logs
    # there alongside uvicorn output. Reconfiguration is idempotent.
    enable_stderr_logging(log_level)
    static_dir = None if dev else WEB_DIST_DIR

    if not dev and not (static_dir and static_dir.is_dir()):
        logger.error(
            "web_dist not found — run 'npm run build --prefix src/kohakuterrarium-frontend' first, "
            "or use --dev mode",
            path=str(WEB_DIST_DIR),
        )
        sys.exit(1)

    if mode == "lab-host":
        if not lab_token:
            logger.error("lab-host mode requires --lab-token")
            sys.exit(1)
        if not lab_bind:
            lab_bind = "127.0.0.1:8100"
        logger.info(
            "boot mode: lab-host",
            lab_bind=lab_bind,
            token_present=bool(lab_token),
        )
        print(f"Lab-host mode: Lab transport on ws://{lab_bind}")
    else:
        logger.info("boot mode: standalone", host=host, port=port)

    creatures_dirs, terrariums_dirs = _resolve_config_dirs()
    mark_startup(
        "web_config_dirs_resolved",
        surface="web",
        creatures=len(creatures_dirs),
        terrariums=len(terrariums_dirs),
    )

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=static_dir,
        lab_mode=mode,
        lab_bind=lab_bind,
        lab_token=lab_token,
    )
    mark_startup("web_app_created", surface="web")

    # Probe forward so direct web serving can tolerate a busy requested port.
    try:
        port = find_free_port(start=port, host=host)
    except RuntimeError as e:
        logger.error("Port allocation failed", error=str(e))
        sys.exit(1)

    _publish_actual_port(state_path, host, port)

    if dev:
        print(f"API-only mode on http://{host}:{port}")
        print(
            "Start vite dev server separately: "
            "npm run dev --prefix src/kohakuterrarium-frontend"
        )
    else:
        print(f"KohakuTerrarium web UI: http://{host}:{port}")

    mark_startup("web_server_run", surface="web", host=host, port=port)
    uvicorn.run(app, host=host, port=port)


def _is_briefcase_runtime() -> bool:
    """Return whether the process uses the non-detachable Briefcase runtime.

    Detection accepts the launcher's explicit environment marker or the
    isolated path file placed beside a Briefcase stub executable.
    """
    if os.environ.get("KT_LAUNCHER_EXEC") == "1":
        return True
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return False
    return any(exe_dir.glob("python3*._pth"))


def run_desktop_app(port: int = 8001, log_level: str = "INFO") -> None:
    """Launch the desktop app in-process for Briefcase or detached otherwise.

    Regular interpreters spawn a child and redirect its output to ``app.log``.
    Briefcase must run the server and window in the current process because its
    application stub does not support ``python -m`` execution.
    """
    if _is_briefcase_runtime():
        # The Briefcase stub is the GUI process and cannot be relaunched with
        # module arguments, so it must own uvicorn and pywebview directly.
        _run_desktop_app_blocking(port=port, log_level=log_level)
        return

    cmd = [
        sys.executable,
        "-m",
        "kohakuterrarium.serving.web",
        "--port",
        str(port),
        "--log-level",
        str(log_level),
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

    mark_startup("desktop_child_spawn_begin", surface="desktop", port=port)
    child = subprocess.Popen(cmd, **kwargs)
    mark_startup(
        "desktop_child_spawned", surface="desktop", port=port, child_pid=child.pid
    )
    print(f"KohakuTerrarium desktop app launched (port {port})")
    print(f"  Log: {log_dir / 'app.log'}")


def _run_desktop_app_blocking(port: int = 8001, log_level: str = "INFO") -> None:
    """Run uvicorn and the native desktop window until the UI closes."""
    configure_utf8_stdio(log=True)
    os.environ["KT_STARTUP_SURFACE"] = "desktop"
    enable_file_logging()

    # A stable application ID lets Windows associate the packaged taskbar icon.
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "KohakuLab.KohakuTerrarium"
            )
        except Exception:
            pass

    set_level(log_level)
    enable_stderr_logging(log_level)

    try:
        import webview
    except ImportError:
        print("pywebview is required for 'kt app'.")
        print("Install: pip install 'KohakuTerrarium[desktop]'")
        sys.exit(1)

    if not WEB_DIST_DIR.is_dir():
        logger.error(
            "web_dist not found — run 'npm run build --prefix src/kohakuterrarium-frontend' first",
            path=str(WEB_DIST_DIR),
        )
        sys.exit(1)

    creatures_dirs, terrariums_dirs = _resolve_config_dirs()
    mark_startup(
        "desktop_config_dirs_resolved",
        surface="desktop",
        creatures=len(creatures_dirs),
        terrariums=len(terrariums_dirs),
    )

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=WEB_DIST_DIR,
    )
    mark_startup("desktop_app_created", surface="desktop")

    # Open webview only after uvicorn reports the actual bound port.
    try:
        _server, port = start_uvicorn_with_port_fallback(
            app,
            requested_port=port,
            host="127.0.0.1",
            log_level="warning",
        )
    except RuntimeError as e:
        logger.error("Failed to start uvicorn", error=str(e))
        sys.exit(1)
    logger.info("desktop: uvicorn listening at http://127.0.0.1:%d", port)
    mark_startup("desktop_server_ready", surface="desktop", port=port)

    icons_dir = Path(__file__).parent.parent / "app_icons"
    icon_ico = icons_dir / "window.ico"
    icon_png = icons_dir / "window.png"

    window = webview.create_window(
        "KohakuTerrarium",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=800,
        min_size=(800, 500),
        zoomable=True,
        text_select=True,
        confirm_close=True,
        background_color="#1a1a2e",
    )
    mark_startup("desktop_window_created", surface="desktop", port=port)

    def _set_icon_windows():
        try:
            user32 = ctypes.windll.user32
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1
            ico = str(icon_ico) if icon_ico.exists() else None
            if not ico:
                return
            hicon = user32.LoadImageW(None, ico, 1, 0, 0, 0x00000010)
            if not hicon:
                return
            hwnd = user32.FindWindowW(None, "KohakuTerrarium")
            if hwnd:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        except Exception:
            pass

    def _set_icon_macos():
        try:
            if not icon_png.exists():
                return
            from AppKit import NSApp, NSApplication, NSImage

            app = NSApp() or NSApplication.sharedApplication()
            image = NSImage.alloc().initWithContentsOfFile_(str(icon_png))
            if image:
                app.setApplicationIconImage_(image)
        except Exception:
            pass

    if sys.platform == "win32":

        def _on_shown():
            mark_startup("desktop_window_shown", surface="desktop", port=port)
            _set_icon_windows()

        window.events.shown += _on_shown
        webview.start()
    elif sys.platform == "darwin":

        def _on_shown():
            mark_startup("desktop_window_shown", surface="desktop", port=port)
            _set_icon_macos()

        window.events.shown += _on_shown
        webview.start(gui="cocoa")
    else:
        icon_path = str(icon_png) if icon_png.exists() else None
        webview.start(icon=icon_path)


if __name__ == "__main__":
    import argparse as _ap

    _parser = _ap.ArgumentParser()
    _parser.add_argument("--port", type=int, default=8001)
    _parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    _args = _parser.parse_args()
    _run_desktop_app_blocking(port=_args.port, log_level=_args.log_level)
