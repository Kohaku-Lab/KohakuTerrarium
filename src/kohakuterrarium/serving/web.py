"""
Web server and desktop app launcher for KohakuTerrarium.

``kt web``  — FastAPI + built Vue frontend in a single process.
``kt app``  — Same, but wrapped in a native pywebview window.
"""

import ctypes
import os
import socket
import sys
import threading
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# web_dist lives at src/kohakuterrarium/web_dist/ (built by vite)
WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web_dist"


def _resolve_config_dirs() -> tuple[list[str], list[str]]:
    """Resolve creature/terrarium config directories.

    Sources (all merged):
      1. KT_CREATURES_DIRS / KT_TERRARIUMS_DIRS env vars
      2. Installed packages (``~/.kohakuterrarium/packages/``)
      3. Local project dirs (``creatures/``, ``terrariums/`` in project root)
    """
    from kohakuterrarium.packages import PACKAGES_DIR, _get_package_root, list_packages

    creatures: list[str] = []
    terrariums: list[str] = []

    # 1. Env vars (highest priority, explicit override)
    env_creatures = os.environ.get("KT_CREATURES_DIRS")
    if env_creatures:
        creatures.extend(env_creatures.split(","))
    env_terrariums = os.environ.get("KT_TERRARIUMS_DIRS")
    if env_terrariums:
        terrariums.extend(env_terrariums.split(","))

    # 2. Installed packages
    if PACKAGES_DIR.exists():
        for pkg in list_packages():
            pkg_root = _get_package_root(pkg["name"])
            if pkg_root:
                c = pkg_root / "creatures"
                t = pkg_root / "terrariums"
                if c.is_dir():
                    creatures.append(str(c))
                if t.is_dir():
                    terrariums.append(str(t))

    # 3. Current working directory (where the user runs kt web/app from)
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
    """Find a free TCP port starting from ``start``.

    Tries ``start``, ``start+1``, ... up to ``max_tries`` ports.
    Returns the first port that can be bound. Raises RuntimeError if none.
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


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8001,
    dev: bool = False,
) -> None:
    """Start the FastAPI server, optionally serving the built frontend.

    Args:
        host: Bind address.
        port: Bind port.
        dev: If True, skip static file serving (user runs vite dev separately).
    """
    import uvicorn

    from kohakuterrarium.api.app import create_app

    static_dir = None if dev else WEB_DIST_DIR

    if not dev and not (static_dir and static_dir.is_dir()):
        logger.error(
            "web_dist not found — run 'npm run build --prefix src/kohakuterrarium-frontend' first, "
            "or use --dev mode",
            path=str(WEB_DIST_DIR),
        )
        sys.exit(1)

    creatures_dirs, terrariums_dirs = _resolve_config_dirs()

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=static_dir,
    )

    # Auto-find port if requested port is busy
    try:
        port = find_free_port(start=port, host=host)
    except RuntimeError as e:
        logger.error("Port allocation failed", error=str(e))
        sys.exit(1)

    if dev:
        print(f"API-only mode on http://{host}:{port}")
        print(
            "Start vite dev server separately: "
            "npm run dev --prefix src/kohakuterrarium-frontend"
        )
    else:
        print(f"KohakuTerrarium web UI: http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)


def run_desktop_app(port: int = 8001) -> None:
    """Launch the desktop app as a detached process and return immediately.

    The caller's terminal is released right away. The child process
    runs the server + pywebview window independently.

    On Windows, uses ``pythonw.exe`` (the windowless Python interpreter)
    so no console window is created.  On Unix, starts a new session so
    the child survives terminal close.

    Stderr is redirected to ``~/.kohakuterrarium/app.log`` for debugging.
    """
    import subprocess

    # Always use sys.executable — it's the Python that's running kt right now,
    # guaranteed to have the correct env (works with uv, micromamba, venv, etc.)
    cmd = [sys.executable, "-m", "kohakuterrarium.serving.web", "--port", str(port)]

    # Redirect stderr to a log file so crashes aren't silent
    log_dir = Path.home() / ".kohakuterrarium"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "app.log", "w")  # noqa: SIM115

    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }

    if sys.platform == "win32":
        # CREATE_NO_WINDOW prevents python.exe from spawning a console.
        # The child process runs independently — Popen is non-blocking and
        # the child survives after the parent (kt app) exits.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        # Unix: new session so the child survives terminal close
        kwargs["start_new_session"] = True

    subprocess.Popen(cmd, **kwargs)
    print(f"KohakuTerrarium desktop app launched (port {port})")
    print(f"  Log: {log_dir / 'app.log'}")


def _run_desktop_app_blocking(port: int = 8001) -> None:
    """Actually run the desktop app (blocking). Called by the child process."""
    # Set AppUserModelID on Windows so the taskbar shows our icon
    # instead of the generic python.exe icon.
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "KohakuLab.KohakuTerrarium"
            )
        except Exception:
            pass

    try:
        import webview
    except ImportError:
        print("pywebview is required for 'kt app'.")
        print("Install: pip install 'KohakuTerrarium[desktop]'")
        sys.exit(1)

    import uvicorn

    from kohakuterrarium.api.app import create_app

    if not WEB_DIST_DIR.is_dir():
        logger.error(
            "web_dist not found — run 'npm run build --prefix src/kohakuterrarium-frontend' first",
            path=str(WEB_DIST_DIR),
        )
        sys.exit(1)

    creatures_dirs, terrariums_dirs = _resolve_config_dirs()

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=WEB_DIST_DIR,
    )

    # Auto-find free port (multi-instance safe)
    try:
        port = find_free_port(start=port, host="127.0.0.1")
    except RuntimeError as e:
        logger.error("Port allocation failed", error=str(e))
        sys.exit(1)

    # Uvicorn in a daemon thread — dies when the main thread (webview) exits
    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": app,
            "host": "127.0.0.1",
            "port": port,
            "log_level": "warning",
        },
        daemon=True,
    )
    server_thread.start()

    # Resolve icon paths
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

    def _set_icon_windows():
        """Set window + taskbar icon on Windows via win32 API."""
        try:
            user32 = ctypes.windll.user32
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            # Load icon from .ico file
            ico = str(icon_ico) if icon_ico.exists() else None
            if not ico:
                return

            hicon = user32.LoadImageW(
                None, ico, 1, 0, 0, 0x00000010  # IMAGE_ICON  # LR_LOADFROMFILE
            )
            if not hicon:
                return

            # Find the pywebview window by title
            hwnd = user32.FindWindowW(None, "KohakuTerrarium")
            if hwnd:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        except Exception:
            pass

    if sys.platform == "win32":
        # Set icon after window is shown (slight delay for window creation)
        def _on_shown():
            _set_icon_windows()

        window.events.shown += _on_shown
        webview.start()
    else:
        # GTK/QT: icon parameter works natively
        icon_path = str(icon_png) if icon_png.exists() else None
        webview.start(icon=icon_path)


if __name__ == "__main__":
    import argparse as _ap

    _parser = _ap.ArgumentParser()
    _parser.add_argument("--port", type=int, default=8001)
    _args = _parser.parse_args()
    _run_desktop_app_blocking(port=_args.port)
