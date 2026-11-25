"""Shared support for browser-backed tests: a loopback fixture site, the Playwright
browser cache under the suite's redirected HOME, and process liveness checks."""

import ctypes
import os
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "browser" / "site"
Route = tuple[int, dict[str, str], bytes] | Callable[[BaseHTTPRequestHandler], None]


class FixtureSite:
    """A threaded HTTP server on 127.0.0.1 serving ``routes`` then files from ``SITE_DIR``.

    A route is ``(status, headers, body)`` or a callable that writes the
    response itself. ``hits`` counts requests per path; ``bodies`` keeps the
    request bodies each path received.
    """

    def __init__(self, root: Path = SITE_DIR) -> None:
        self.root = root
        self.routes: dict[str, Route] = {}
        self.hits: dict[str, int] = {}
        self.bodies: dict[str, list[bytes]] = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                site._serve(self)

            do_POST = do_GET

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def html(self, path: str, body: str, **headers: str) -> None:
        self.routes[path] = (
            200,
            {"Content-Type": "text/html; charset=utf-8", **headers},
            body.encode(),
        )

    def redirect(self, path: str, location: str, status: int = 302) -> None:
        self.routes[path] = (status, {"Location": location}, b"")

    def file(self, path: str, body: bytes, content_type: str, **headers: str) -> None:
        self.routes[path] = (200, {"Content-Type": content_type, **headers}, body)

    def total_hits(self) -> int:
        return sum(self.hits.values())

    def _serve(self, handler: BaseHTTPRequestHandler) -> None:
        path = handler.path.split("?", 1)[0]
        self.hits[path] = self.hits.get(path, 0) + 1
        length = int(handler.headers.get("Content-Length") or 0)
        if length:
            self.bodies.setdefault(path, []).append(handler.rfile.read(length))
        route = self.routes.get(path)
        if callable(route):
            route(handler)
            return
        if route is None:
            static = (self.root / path.lstrip("/")).resolve()
            if path != "/" and static.is_file() and static.is_relative_to(self.root):
                route = (
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    static.read_bytes(),
                )
            else:
                route = (404, {"Content-Type": "text/plain"}, b"not found")
        status, headers, body = route
        handler.send_response(status)
        for name, value in {"Cache-Control": "no-store", **headers}.items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class UdpSink:
    """A UDP socket on 127.0.0.1 that reports whether anything arrived."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.setblocking(False)
        self.port = self._sock.getsockname()[1]

    def received(self) -> int:
        count = 0
        while True:
            try:
                self._sock.recv(65536)
            except (BlockingIOError, OSError):
                return count
            count += 1

    def close(self) -> None:
        self._sock.close()


def real_browsers_path() -> Path | None:
    """Where Playwright keeps its browsers for the operator (HOME is redirected in tests)."""
    home = Path(os.environ.get("KT_TEST_REAL_HOME") or Path.home())
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "ms-playwright" if base else None
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def browsers_env() -> dict[str, str]:
    """``PLAYWRIGHT_BROWSERS_PATH`` to set when it is unset and the cache exists."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return {}
    path = real_browsers_path()
    return {"PLAYWRIGHT_BROWSERS_PATH": str(path)} if path and path.is_dir() else {}


def process_alive(pid: int) -> bool:
    """Whether process ``pid`` is still running (never signals it).

    A POSIX process that has ended but is not yet reaped by its parent (a
    zombie) is not running.
    """
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return not _ended_unreaped(pid)


def _ended_unreaped(pid: int) -> bool:
    """Whether ``pid`` is a zombie or already gone: ``/proc`` on Linux, ``ps`` elsewhere."""
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8", errors="replace"
            )
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return stat.rpartition(")")[2].split()[:1] in (["Z"], ["X"])
    try:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return state.startswith("Z")


def suspend_process(pid: int) -> None:
    """Freeze process ``pid`` so it answers nothing, the way a hung process does."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0800, False, pid)
        if not handle:
            raise OSError(f"cannot open process {pid} to suspend it")
        try:
            ctypes.windll.ntdll.NtSuspendProcess(handle)
        finally:
            kernel32.CloseHandle(handle)
        return
    os.kill(pid, signal.SIGSTOP)


def kill_process(pid: int) -> None:
    """End process ``pid`` at once."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    os.kill(pid, signal.SIGKILL)
