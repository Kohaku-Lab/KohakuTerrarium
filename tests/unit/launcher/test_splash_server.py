"""Unit tests for the launcher splash HTTP server.

The splash page is loaded into pywebview via the ``html=`` argument
(no http origin), so a same-origin policy treats the polling fetch as
cross-origin.  Without CORS headers the browser drops the response and
the splash sits forever on its hardcoded "Starting…" / 0% defaults.

Window-close callbacks are the other side of that story: ``stop()``
only killed the HTTP server, leaving the pywebview window on screen
forever.  Registered callbacks now fire from ``stop()`` so the window
is torn down with the server.
"""

import json
from http.client import HTTPConnection

import pytest

from kohakuterrarium.launcher.splash_server import ProgressFrame, SplashServer


@pytest.fixture
def server():
    srv = SplashServer().start()
    yield srv
    srv.stop()


def _get(endpoint: str, path: str = "/progress") -> tuple[int, dict[str, str], bytes]:
    # Parse "http://host:port/x" into HTTPConnection-friendly bits.
    scheme_split = endpoint.split("://", 1)[1]
    host_port, _ = scheme_split.split("/", 1)
    host, port = host_port.split(":", 1)
    conn = HTTPConnection(host, int(port), timeout=2.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _options(endpoint: str) -> tuple[int, dict[str, str]]:
    scheme_split = endpoint.split("://", 1)[1]
    host_port, _ = scheme_split.split("/", 1)
    host, port = host_port.split(":", 1)
    conn = HTTPConnection(host, int(port), timeout=2.0)
    try:
        conn.request("OPTIONS", "/progress")
        resp = conn.getresponse()
        resp.read()
        return resp.status, dict(resp.getheaders())
    finally:
        conn.close()


class TestSplashServerCors:
    """Without CORS headers the page never sees a frame."""

    def test_get_progress_emits_cors_origin(self, server):
        status, headers, body = _get(server.endpoint)
        assert status == 200
        # ``about:blank`` origin → wildcard is the only thing the
        # webview will accept without a preflight.
        assert headers.get("Access-Control-Allow-Origin") == "*"
        payload = json.loads(body)
        assert payload["seq"] == 0
        assert payload["percent"] == 0.0

    def test_get_404_still_emits_cors(self, server):
        # A misrouted poll still has to be readable so the JS sees the
        # 404 and stays in its retry loop instead of throwing.
        status, headers, _ = _get(server.endpoint, path="/does-not-exist")
        assert status == 404
        assert headers.get("Access-Control-Allow-Origin") == "*"

    def test_options_preflight_succeeds(self, server):
        status, headers = _options(server.endpoint)
        assert status == 204
        assert headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in headers.get("Access-Control-Allow-Methods", "")


class TestSplashServerStopClosesWindow:
    """``stop()`` must invoke registered window-close callbacks."""

    def test_close_callback_invoked_on_stop(self):
        srv = SplashServer().start()
        calls: list[int] = []
        srv.register_close_callback(lambda: calls.append(1))
        srv.stop()
        assert calls == [1]

    def test_multiple_callbacks_all_invoked(self):
        srv = SplashServer().start()
        calls: list[str] = []
        srv.register_close_callback(lambda: calls.append("a"))
        srv.register_close_callback(lambda: calls.append("b"))
        srv.stop()
        assert calls == ["a", "b"]

    def test_raising_callback_does_not_block_others(self):
        # If pywebview's destroy raises (window already gone, race
        # with the user closing it manually), the Tk fallback or any
        # other backend MUST still get its callback fired.
        srv = SplashServer().start()
        survivors: list[str] = []

        def _boom() -> None:
            raise RuntimeError("pywebview destroy failed")

        srv.register_close_callback(_boom)
        srv.register_close_callback(lambda: survivors.append("tk"))
        srv.stop()
        assert survivors == ["tk"]

    def test_stop_clears_callbacks_so_second_stop_is_idempotent(self):
        srv = SplashServer().start()
        calls: list[int] = []
        srv.register_close_callback(lambda: calls.append(1))
        srv.stop()
        srv.stop()  # idempotent — must not re-invoke
        assert calls == [1]


class TestSplashServerPublish:
    def test_publish_advances_seq_and_overlays_fields(self, server):
        server.publish("Setting up", percent=5)
        frame = server.snapshot()
        assert frame.seq == 1
        assert frame.phase == "Setting up"
        assert frame.percent == 5.0

        server.publish(percent=42, message="extracting kohakuterrarium-…")
        frame2 = server.snapshot()
        assert frame2.seq == 2
        # phase carries forward when omitted
        assert frame2.phase == "Setting up"
        assert frame2.percent == 42.0
        assert frame2.message == "extracting kohakuterrarium-…"

    def test_published_frame_visible_over_http(self, server):
        server.publish("Done", percent=100, status="ok")
        status, _, body = _get(server.endpoint)
        assert status == 200
        payload = json.loads(body)
        assert payload["phase"] == "Done"
        assert payload["percent"] == 100.0
        assert payload["status"] == "ok"


class TestProgressFrameDefaults:
    def test_initial_frame_is_starting_zero(self):
        frame = ProgressFrame()
        assert frame.seq == 0
        assert frame.phase == ""
        assert frame.percent == 0.0
        # ``None`` keeps the JS polling — only ok/failed terminate.
        assert frame.status is None
