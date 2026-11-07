"""Serve the web application: FastAPI with the built Vue frontend.

The native desktop window lives in ``serving/desktop/``.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

import kohakuterrarium.session.run_state as run_state
from kohakuterrarium.packages.locations import get_package_root, packages_dir
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.fd_limit import raise_fd_limit
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)


def create_app(**kwargs):
    from kohakuterrarium.api.app import create_app

    return create_app(**kwargs)


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
                server._kt_thread = thread
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
    idle_exit: float | None = None,
) -> None:
    """Run the FastAPI service in standalone or authenticated lab-host mode.

    ``idle_exit``: stop once no session has run for that many seconds (a server started on
    demand by ktui).

    Development mode omits static frontend serving. Daemon callers may provide
    ``state_path`` to receive the selected port. Lab-host mode also starts the
    WebSocket transport at ``lab_bind`` and requires ``lab_token``.
    """
    configure_utf8_stdio(log=True)
    raise_fd_limit(log=True)

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

    # The requested port names this server even if the bound port drifts.
    run_state.SERVER_KEY = f"port:{port}"
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
    if not idle_exit:
        uvicorn.run(app, host=host, port=port)
        return
    # a server started on demand needs a handle to stop itself once unused
    activity = _Activity(app)
    server = uvicorn.Server(uvicorn.Config(activity, host=host, port=port))
    threading.Thread(
        target=_exit_when_idle,
        args=(server, activity, _probe_url(host, port), idle_exit),
        daemon=True,
        name="kt-idle-exit",
    ).start()
    server.run()


IDLE_POLL = 15.0
# the idle check's own requests carry this header and do not count as use
PROBE_HEADER = "X-KT-Idle-Probe"


class _Activity:
    """The app, noting when it was last used: every request and WebSocket (the web UI open
    in a browser, a ktui attached) except the idle check's own."""

    def __init__(self, app) -> None:
        self.app = app
        self.last = time.monotonic()
        self.open = 0

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in ("http", "websocket") or any(
            key == PROBE_HEADER.lower().encode() for key, _ in scope.get("headers", [])
        ):
            await self.app(scope, receive, send)
            return
        self.open += 1
        self.last = time.monotonic()
        try:
            await self.app(scope, receive, send)
        finally:
            self.open -= 1
            self.last = time.monotonic()


def _probe_url(host: str, port: int) -> str:
    if host in ("0.0.0.0", "", "127.0.0.1"):
        return f"http://127.0.0.1:{port}"
    if host == "::":
        return f"http://[::1]:{port}"
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def _exit_when_idle(
    server: "uvicorn.Server", activity: _Activity, url: str, idle: float
) -> None:
    """Asks ``server`` to stop once, for ``idle`` seconds, it has run no session and nobody
    has used it (no request, no open socket). A listing it cannot read (refused, needs a
    token it lacks) counts as busy: then it does not stop by itself."""
    token = os.environ.get("KT_HOST_TOKEN")
    headers = {PROBE_HEADER: "1", **({"X-KT-Host-Token": token} if token else {})}
    quiet_since = time.monotonic()
    warned = False
    while not server.should_exit:
        time.sleep(IDLE_POLL)
        try:
            response = httpx.get(
                f"{url}/api/sessions/active",
                headers=headers,
                timeout=10,
                trust_env=False,
            )
            if response.status_code >= 400:
                raise ValueError(f"HTTP {response.status_code}")
            sessions = bool(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            if not warned:
                logger.warning(
                    "idle check cannot list sessions; staying up", error=str(exc)
                )
                warned = True
            sessions = True
        now = time.monotonic()
        if sessions or activity.open:
            quiet_since = now
            continue
        quiet = now - max(quiet_since, activity.last)
        if quiet >= idle:
            logger.info("unused: stopping (started on demand)", idle_seconds=idle)
            server.should_exit = True
