"""In-process HTTP server feeding the splash page progress frames.

Binds to ``127.0.0.1:0`` (kernel picks an ephemeral port) so two
splash instances can't collide.  Only handles ``GET /progress``;
anything else returns 404.  Single-threaded — the splash page polls
at 250-800ms intervals so a single thread is plenty.
"""

import json
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from kohakuterrarium.launcher.log import get_logger


@dataclass
class ProgressFrame:
    """One progress update the splash page renders."""

    seq: int = 0
    phase: str = ""
    percent: float = 0.0
    message: str = ""
    # Terminal frames carry ``"ok"`` / ``"failed"``; in-progress frames
    # have ``None`` here so the page keeps polling.
    status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SplashServer:
    """Encapsulates the HTTP server + the in-memory current frame.

    Caller pattern::

        srv = SplashServer().start()
        srv.publish("Creating venv", percent=10)
        ...
        srv.publish("Done", percent=100, status="ok")
        srv.stop()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame = ProgressFrame()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        # Backend-specific window teardown callbacks.  pywebview /
        # Tk register theirs at open time so ``stop()`` can close the
        # window — without this the HTTP server shuts down but the
        # pywebview splash sits on screen forever (Tk auto-closes on
        # terminal status from inside its own poll loop, so it
        # registers a no-op here).
        self._close_callbacks: list = []

    def register_close_callback(self, callback) -> None:
        """Register a callable invoked from :meth:`stop` to close the UI."""
        self._close_callbacks.append(callback)

    @property
    def endpoint(self) -> str:
        if self._server is None:
            raise RuntimeError("SplashServer not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/progress"

    def publish(
        self,
        phase: str | None = None,
        *,
        percent: float | None = None,
        message: str | None = None,
        status: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._frame = ProgressFrame(
                seq=self._frame.seq + 1,
                phase=phase if phase is not None else self._frame.phase,
                percent=percent if percent is not None else self._frame.percent,
                message=message if message is not None else self._frame.message,
                status=status,
                extra=extra if extra is not None else self._frame.extra,
            )

    def snapshot(self) -> ProgressFrame:
        with self._lock:
            # Return a copy so callers can mutate freely.
            return ProgressFrame(**asdict(self._frame))

    def start(self) -> "SplashServer":
        if self._server is not None:
            return self
        srv_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(
                self, fmt, *args
            ):  # noqa: D401 - silence default access log
                pass

            def _emit_cors(self) -> None:
                # The splash page is loaded into pywebview via the
                # ``html=`` argument, which gives it an ``about:blank`` /
                # ``data:`` origin.  A ``fetch`` from that origin to
                # ``http://127.0.0.1:<port>/progress`` is cross-origin,
                # and without these headers the browser drops the
                # response so the page sits forever on its hardcoded
                # "Starting…" / 0% defaults.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def do_OPTIONS(self):  # noqa: N802
                self.send_response(204)
                self._emit_cors()
                self.end_headers()

            def do_GET(self):  # noqa: N802
                if not self.path.startswith("/progress"):
                    self.send_response(404)
                    self._emit_cors()
                    self.end_headers()
                    return
                frame = srv_ref.snapshot()
                body = json.dumps(asdict(frame)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._emit_cors()
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="kt-splash-server",
            daemon=True,
        )
        self._thread.start()
        get_logger().info("splash: listening at %s", self.endpoint)
        return self

    def stop(self) -> None:
        # Close the window FIRST so the user sees the splash disappear
        # immediately; the HTTP server teardown happens after.  Order
        # matters: if we kill the HTTP server first, the still-open
        # pywebview window's JS poll starts failing and the page
        # visibly degrades before the window itself disappears.
        for cb in self._close_callbacks:
            try:
                cb()
            except Exception:
                get_logger().warning("splash: close callback raised", exc_info=True)
        self._close_callbacks.clear()
        if self._server is None:
            return
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


__all__ = ["ProgressFrame", "SplashServer"]
