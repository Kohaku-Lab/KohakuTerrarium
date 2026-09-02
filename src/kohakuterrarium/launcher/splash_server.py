"""Serve the splash page and its progress frames over a loopback endpoint.

The server answers ``/`` with the splash page (when constructed with
``page_html``) and ``/progress`` with the current frame; anything else is a
404. Serving the page from the same origin as its feed keeps the poll
same-origin, so neither CORS nor the browser's private-network gate can
strand it. An ephemeral port avoids collisions between concurrent launcher
instances, and a single request thread is sufficient for the splash page's
periodic polling.
"""

import json
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from kohakuterrarium.launcher.log import get_logger


@dataclass
class ProgressFrame:
    """Represent one renderable splash progress update."""

    seq: int = 0
    phase: str = ""
    percent: float = 0.0
    message: str = ""
    # A missing status keeps polling active; terminal frames use ``ok`` or
    # ``failed`` to stop the splash page.
    status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class SplashServer:
    """Manage the splash HTTP endpoint and its current progress frame."""

    def __init__(self, page_html: str | None = None) -> None:
        self._lock = threading.Lock()
        self._frame = ProgressFrame()
        self._page_html = page_html
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        # UI backends register teardown here so stopping the progress server
        # cannot leave a detached splash window open.
        self._close_callbacks: list = []

    def register_close_callback(self, callback) -> None:
        """Register a callable invoked from :meth:`stop` to close the UI."""
        self._close_callbacks.append(callback)

    @property
    def endpoint(self) -> str:
        return self.page_url + "progress"

    @property
    def page_url(self) -> str:
        if self._server is None:
            raise RuntimeError("SplashServer not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def publish(
        self,
        phase: str | None = None,
        *,
        percent: float | None = None,
        message: str | None = None,
        status: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Publish a new frame, retaining fields omitted by the caller."""
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
        """Return an independent copy of the current progress frame."""
        with self._lock:
            return ProgressFrame(**asdict(self._frame))

    def start(self) -> "SplashServer":
        """Start the loopback server once and return this instance."""
        if self._server is not None:
            return self
        srv_ref = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(
                self, fmt, *args
            ):  # noqa: D401 - silence default access log
                pass

            def _emit_cors(self) -> None:
                # Harmless for the same-origin page; keeps an embedder that
                # loads the page from another origin working too.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def _send(self, content_type: str, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._emit_cors()
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):  # noqa: N802
                self.send_response(204)
                self._emit_cors()
                self.end_headers()

            def do_GET(self):  # noqa: N802
                if self.path.startswith("/progress"):
                    frame = srv_ref.snapshot()
                    body = json.dumps(asdict(frame)).encode("utf-8")
                    self._send("application/json", body)
                    return
                page = srv_ref._page_html
                if page is not None and self.path.split("?", 1)[0] == "/":
                    self._send("text/html; charset=utf-8", page.encode("utf-8"))
                    return
                self.send_response(404)
                self._emit_cors()
                self.end_headers()

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
        """Close registered splash windows, then stop the HTTP server."""
        # Close the UI before its progress endpoint so the visible page never
        # enters a failed polling state during teardown.
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
