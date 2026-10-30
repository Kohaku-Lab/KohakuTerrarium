"""Desktop launch token: one-time exchange for a session cookie, loopback only."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.auth.launch_token import (
    COOKIE_NAME,
    LAUNCH_ENV,
    QUERY_PARAM,
    LaunchGate,
    LaunchTokenMiddleware,
)

TOKEN = "launch-secret"
WS_URL = "ws://127.0.0.1:8001/ws/echo"


async def _page(request):
    return PlainTextResponse("page")


async def _socket(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def _client(base="http://127.0.0.1:8001"):
    app = Starlette(
        routes=[
            Route("/", _page),
            Route("/api/ping", _page),
            Route("/healthz", _page),
            WebSocketRoute("/ws/echo", _socket),
        ]
    )
    wrapped = LaunchTokenMiddleware(app, LaunchGate(TOKEN))
    return TestClient(wrapped, base_url=base, follow_redirects=False)


def _exchange(client):
    return client.get(f"/?{QUERY_PARAM}={TOKEN}")


def test_requests_without_the_cookie_are_refused():
    client = _client()
    assert client.get("/").status_code == 401
    assert client.get("/api/ping").status_code == 401


def test_health_probes_stay_open():
    assert _client().get("/healthz").text == "page"


def test_token_is_exchanged_for_a_strict_httponly_cookie_and_stripped_from_the_url():
    client = _client()
    response = _exchange(client)
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert TOKEN not in cookie


def test_other_query_parameters_survive_the_redirect():
    client = _client()
    response = client.get(f"/api/ping?a=1&{QUERY_PARAM}={TOKEN}&b=2")
    assert response.headers["location"] == "/api/ping?a=1&b=2"


def test_cookie_unlocks_http_and_websocket():
    client = _client()
    _exchange(client)
    assert client.get("/api/ping").text == "page"
    with client.websocket_connect(WS_URL) as socket:
        assert socket.receive_text() == "hello"


def test_wrong_token_is_refused_and_sets_no_cookie():
    client = _client()
    response = client.get(f"/?{QUERY_PARAM}=nope")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_token_works_once():
    client = _client()
    assert _exchange(client).status_code == 302
    other = TestClient(client.app, base_url="http://127.0.0.1:8001")
    assert other.get(f"/?{QUERY_PARAM}={TOKEN}").status_code == 401


def test_websocket_without_the_cookie_is_closed_with_4401():
    client = _client()
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(WS_URL):
            pass
    assert closed.value.code == 4401
    assert closed.value.reason == "launch token required"


def test_non_loopback_host_is_refused_even_with_the_cookie():
    client = _client()
    _exchange(client)
    rebound = client.get("/api/ping", headers={"host": "evil.example:8001"})
    assert rebound.status_code == 403


def test_foreign_origin_is_refused_even_with_the_cookie():
    client = _client()
    _exchange(client)
    assert (
        client.get("/api/ping", headers={"origin": "http://evil.example"}).status_code
        == 403
    )
    assert (
        client.get("/api/ping", headers={"origin": "http://127.0.0.1:9999"}).status_code
        == 403
    )
    assert (
        client.get("/api/ping", headers={"origin": "http://127.0.0.1:8001"}).status_code
        == 200
    )


def test_gate_reads_and_removes_the_environment_token(monkeypatch):
    monkeypatch.setenv(LAUNCH_ENV, TOKEN)
    gate = LaunchGate.from_env()
    assert gate is not None
    assert LAUNCH_ENV not in __import__("os").environ
    assert LaunchGate.from_env() is None


def test_gate_is_disabled_without_a_token(monkeypatch):
    monkeypatch.delenv(LAUNCH_ENV, raising=False)
    assert LaunchGate.from_env() is None
    monkeypatch.setenv(LAUNCH_ENV, "   ")
    assert LaunchGate.from_env() is None
