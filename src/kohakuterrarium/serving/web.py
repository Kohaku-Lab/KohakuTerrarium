"""
Web server and desktop app launcher for KohakuTerrarium.

``kt web``  — FastAPI + built Vue frontend in a single process.
``kt app``  — Same, but wrapped in a native pywebview window.
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
from kohakuterrarium.packages.locations import PACKAGES_DIR, get_package_root
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)

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
            pkg_root = get_package_root(pkg["name"])
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

    Note: this is a *probe* — the returned port can theoretically be
    grabbed by another process between probe close and the real bind
    (TOCTOU race). For the desktop / web entrypoints, prefer
    :func:`start_uvicorn_with_port_fallback`, which retries the start
    until uvicorn actually binds and returns the verified port.
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
    """Start uvicorn in a daemon thread, returning ``(server, port)``.

    Handles two failure modes that the bare ``threading.Thread(uvicorn.run)``
    pattern silently swallows:

    1. **TOCTOU between ``find_free_port`` and the real bind** — if
       another process grabs the probed port in the interim, uvicorn's
       startup task raises ``OSError`` and the daemon thread dies.
       This helper detects the dead thread and retries the next port.
    2. **Webview pointed at a dead port** — the previous code passed
       the *requested* port to ``webview.create_window`` even if
       uvicorn never bound it. Here we wait for ``server.started`` to
       flip true and read the port from ``server.servers[0].sockets[0]``
       so the caller always gets the verified-bound port.

    The returned ``server`` is a live ``uvicorn.Server`` instance; the
    caller can call ``server.should_exit = True`` to shut it down.
    The thread is daemonised so the process can exit cleanly when the
    webview window closes.
    """
    last_exc: Exception | None = None
    for offset in range(max_tries):
        port = requested_port + offset
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
        server = uvicorn.Server(config)
        # Disable uvicorn's signal-handler install — pywebview owns the
        # main thread on Windows / macOS, and uvicorn's ``signal.signal``
        # calls from a worker thread raise ``ValueError: signal only
        # works in main thread of the main interpreter``.
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
                # Bind failed; uvicorn's startup task raised + the
                # thread exited. Try the next port.
                last_exc = RuntimeError(
                    f"uvicorn thread for port {port} died before binding"
                )
                break
            time.sleep(0.05)
        else:
            # Timed out waiting for startup — try to shut it down and
            # move on. Don't accumulate stuck threads across retries.
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
    """Update daemon state file with the actual bound port.

    No-op if state_path is None (e.g. ``kt web`` direct invocation) or the
    file does not exist. CLI polls this file's ``bound`` field after spawn.
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
    """Start the FastAPI server, optionally serving the built frontend.

    Args:
        host: Bind address.
        port: Bind port.
        dev: If True, skip static file serving (user runs vite dev separately).
        state_path: When set (daemon mode), write the actual bound port back
            to this JSON file so the launching CLI can show the truth.
        mode: ``"standalone"`` or ``"lab-host"``.  In lab-host mode a
            :class:`HostEngine` is started in the FastAPI lifespan and a
            :class:`MultiNodeTerrariumService` is installed for the API
            routes that consume :func:`get_service`.
        lab_bind: ``host:port`` for the Lab WebSocket transport (lab-host
            only).  Defaults to ``127.0.0.1:8100``.
        lab_token: shared token clients must present (lab-host only).
            Required when ``mode == "lab-host"``.
    """
    configure_utf8_stdio(log=True)

    set_level(log_level)
    # Mirror kohakuterrarium logs to stderr so the daemon's redirected
    # stderr (~/.kohakuterrarium/run/web.log) captures BOTH uvicorn AND
    # our own logger output. Without this, ``kt serve logs`` shows only
    # uvicorn — our INFO logs (e.g. "Event buffered for mid-turn
    # injection", "Drained N mid-turn buffered event(s)") get
    # silently routed to a separate file (~/.kohakuterrarium/logs/kt.log)
    # the user has no reason to know about. Idempotent: if stderr
    # logging is already on (foreground path), this just resets the
    # level.
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

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=static_dir,
        lab_mode=mode,
        lab_bind=lab_bind,
        lab_token=lab_token,
    )

    # Auto-find port if requested port is busy
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

    uvicorn.run(app, host=host, port=port)


def _is_briefcase_runtime() -> bool:
    """True when this process is running inside a Briefcase desktop bundle.

    Briefcase Windows shells lay down ``python3XX._pth`` next to
    ``sys.executable`` (which is the briefcase STUB binary, not a real
    Python). We can't subprocess-detach via ``sys.executable -m ...``
    because the stub doesn't honour ``-m`` — it routes argv straight
    into the framework CLI parser.

    Detection mirrors ``kohakuterrarium.__main__._is_briefcase_bundle``;
    the launcher additionally sets ``KT_LAUNCHER_EXEC=1`` after its
    in-process sys.path swap so we have a second positive signal.
    """
    if os.environ.get("KT_LAUNCHER_EXEC") == "1":
        return True
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return False
    return any(exe_dir.glob("python3*._pth"))


def run_desktop_app(port: int = 8001, log_level: str = "INFO") -> None:
    """Launch the desktop app.

    Two paths:

    - **CLI / dev** (regular Python interpreter): detach a child process
      via ``Popen`` so the caller's terminal is freed. Child writes
      stderr to ``~/.kohakuterrarium/app.log``.
    - **Briefcase desktop bundle**: there is no detachable Python — the
      briefcase stub doesn't honour ``-m``. Run the server + pywebview
      in this same process via :func:`_run_desktop_app_blocking`. The
      briefcase stub IS the desktop app; releasing a terminal is
      meaningless here.
    """
    if _is_briefcase_runtime():
        # The briefcase stub IS the GUI process. Inline the desktop
        # entry so the same process drives uvicorn + pywebview to
        # completion. Spawning ``sys.executable -m kohakuterrarium.serving.web``
        # would re-enter the briefcase CLI parser and exit silently
        # with an "argument command: invalid choice" — exactly the
        # "runs for a while then turns off" symptom seen in dev5.
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

    subprocess.Popen(cmd, **kwargs)
    print(f"KohakuTerrarium desktop app launched (port {port})")
    print(f"  Log: {log_dir / 'app.log'}")


def _run_desktop_app_blocking(port: int = 8001, log_level: str = "INFO") -> None:
    """Actually run the desktop app (blocking). Called by the child process."""
    configure_utf8_stdio(log=True)

    # Set AppUserModelID on Windows so the taskbar shows our icon
    # instead of the generic python.exe icon.
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

    app = create_app(
        creatures_dirs=creatures_dirs,
        terrariums_dirs=terrariums_dirs,
        static_dir=WEB_DIST_DIR,
    )

    # Start uvicorn with fallback + verified bound port.  ``port`` from
    # here on is the ACTUAL port uvicorn is listening on, not the
    # requested one — webview opens against this.
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
            _set_icon_windows()

        window.events.shown += _on_shown
        webview.start()
    elif sys.platform == "darwin":

        def _on_shown():
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
