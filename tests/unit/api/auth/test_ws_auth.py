"""Unit tests for :func:`accept_with_auth_echo`.

The helper is a thin drop-in replacement for ``websocket.accept()``
that echoes a matching ``kt-token.*`` / ``kt-session.*`` sub-protocol
back when the client offered one.  Auth itself is gated upstream by
:class:`HostTokenMiddleware`; this helper handles browser handshake
polish.
"""

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.ws_auth import accept_with_auth_echo


def _make_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.websocket("/ws/echo")
    async def echo(websocket: WebSocket) -> None:
        await accept_with_auth_echo(websocket)
        text = await websocket.receive_text()
        await websocket.send_text(text)
        await websocket.close()

    app.include_router(router)
    return app


class TestEcho:
    def test_no_subprotocol_offered_accepts_plain(self):
        with TestClient(_make_app()) as client:
            with client.websocket_connect("/ws/echo") as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "hi"
                assert ws.accepted_subprotocol in (None, "")

    def test_kt_token_subprotocol_is_echoed(self):
        # Send + receive first so the server-side handler completes its
        # full cycle before the ``with`` block exits — otherwise
        # ``await websocket.receive_text()`` gets a disconnect when the
        # client closes and the test sees the exception bubble up.
        with TestClient(_make_app()) as client:
            with client.websocket_connect(
                "/ws/echo", subprotocols=["kt-token.abc"]
            ) as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "ping"
                assert ws.accepted_subprotocol == "kt-token.abc"

    def test_kt_session_subprotocol_is_echoed(self):
        with TestClient(_make_app()) as client:
            with client.websocket_connect(
                "/ws/echo", subprotocols=["kt-session.deadbeef"]
            ) as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "ping"
                assert ws.accepted_subprotocol == "kt-session.deadbeef"

    def test_non_auth_subprotocols_not_echoed(self):
        # Client offered only non-KT sub-protocols — we don't pick any
        # of them.  Browsers using these protocols would normally fail,
        # but that's their choice; our contract is "echo KT auth proto
        # if offered, else nothing."
        with TestClient(_make_app()) as client:
            with client.websocket_connect(
                "/ws/echo", subprotocols=["chat", "binary"]
            ) as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "ping"
                assert ws.accepted_subprotocol is None

    def test_kt_subprotocol_picked_among_many(self):
        # Mix of auth + non-auth — first KT auth wins.
        with TestClient(_make_app()) as client:
            with client.websocket_connect(
                "/ws/echo",
                subprotocols=["chat", "kt-token.tok", "kt-session.sess"],
            ) as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "ping"
                assert ws.accepted_subprotocol == "kt-token.tok"
