"""Unit tests for the launcher splash HTTP server.

The server feeds the splash page its progress frames and, since the
page is served from the same origin, the poll never crosses an origin
boundary.  CORS headers stay on the feed so an embedder loading the
page from elsewhere still gets frames instead of a silently dropped
response and a splash stuck on its "Starting…" / 0% defaults.

Window-close callbacks registered on the server fire from ``stop()``
so any UI bound to it is torn down together with the server.
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


class TestSplashServerPage:
    """The splash page is served by the progress server itself.

    Loading it from the same ``http://127.0.0.1:<port>`` origin as
    ``/progress`` keeps the poll same-origin, so neither CORS nor the
    browser's private-network gate can strand the page on its defaults.
    """

    def test_page_url_serves_html_and_progress_stays_json(self):
        srv = SplashServer(page_html="<html><body>splash</body></html>").start()
        try:
            assert srv.page_url == srv.endpoint[: -len("progress")]
            status, headers, body = _get(srv.endpoint, path="/")
            assert status == 200
            assert headers.get("Content-Type", "").startswith("text/html")
            assert body == b"<html><body>splash</body></html>"
            status, headers, body = _get(srv.endpoint)
            assert status == 200
            assert headers.get("Content-Type") == "application/json"
            assert json.loads(body)["seq"] == 0
        finally:
            srv.stop()

    def test_page_is_404_without_html(self, server):
        status, _, _ = _get(server.endpoint, path="/")
        assert status == 404

    def test_page_url_requires_started_server(self):
        with pytest.raises(RuntimeError):
            SplashServer(page_html="x").page_url
