"""Unit tests for ``api/ext/owner_routes.py`` — owner RPC over HTTP and WS."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.ext.owner_routes import (
    OUTBOX_LIMIT,
    BoundedOutbox,
    OwnerSocketSession,
)
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.modules.ui_owner import OwnerArgumentError
from kohakuterrarium.studio.identity import ui_extensions
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.testing.terrarium import _FakeAgent


class CounterPlugin(BasePlugin):
    name = "counter"

    def __init__(self):
        super().__init__()
        self.count = 0

    def ui_state(self):
        return {"count": self.count}

    def ui_call(self, action, args):
        if action == "add":
            self.count += int(args.get("n", 1))
            self.ui_emit("state", self.ui_state())
            return self.count
        if action == "explode":
            raise ValueError("an owner bug, not a bad argument")
        raise OwnerArgumentError(f"unknown action {action}")


def _creature(cid: str, with_plugin: bool = True) -> Creature:
    agent = _FakeAgent(cid)
    agent.plugins = PluginManager()
    if with_plugin:
        agent.plugins.register(CounterPlugin())
    return Creature(creature_id=cid, name=cid, agent=agent)


@pytest.fixture
def runtime(reset_service):
    engine = Terrarium()
    set_service(LocalTerrariumService(engine))
    with TestClient(create_app()) as client:
        a = client.portal.call(engine.add_creature, _creature("alice"))
        b = client.portal.call(engine.add_creature, _creature("bob", with_plugin=False))
        yield client, engine, a, b


def _ref(creature: Creature, owner: str = "plugin:counter", **extra) -> dict:
    return {
        "owner": owner,
        "session_id": creature.graph_id,
        "creature_id": creature.name,
        **extra,
    }


def test_runtime_owner_state_call_and_push(runtime):
    client, _engine, alice, _bob = runtime
    resp = client.post("/api/extensions/owner/state", json=_ref(alice))
    assert resp.status_code == 200 and resp.json()["state"] == {"count": 0}

    with client.websocket_connect("/ws/extensions/owner") as ws:
        ws.send_json({"op": "subscribe", "id": "s1", **_ref(alice)})
        assert ws.receive_json() == {
            "type": "subscribed",
            "id": "s1",
            "owner": "plugin:counter",
        }
        resp = client.post(
            "/api/extensions/owner/call", json=_ref(alice, action="add", args={"n": 3})
        )
        assert resp.json() == {"owner": "plugin:counter", "result": 3}
        assert ws.receive_json() == {
            "type": "event",
            "id": "s1",
            "event": "state",
            "data": {"count": 3},
        }
        ws.send_json({"op": "unsubscribe", "id": "s1"})
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        client.post("/api/extensions/owner/call", json=_ref(alice, action="add"))
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_only_owner_argument_errors_are_bad_requests(runtime):
    client, _engine, alice, _bob = runtime
    bad = client.post("/api/extensions/owner/call", json=_ref(alice, action="nope"))
    assert bad.status_code == 400 and bad.json()["detail"]["error"] == "bad_request"
    bug = client.post("/api/extensions/owner/call", json=_ref(alice, action="explode"))
    assert bug.status_code == 500 and bug.json()["detail"]["error"] == "owner_failed"
    # A framework-side bad request (missing scope) stays a 400.
    resp = client.post("/api/extensions/owner/state", json={"owner": "plugin:counter"})
    assert resp.status_code == 400


def test_runtime_owner_is_scoped_to_its_session_and_loaded_plugins(runtime):
    client, _engine, alice, bob = runtime
    cross = {
        "owner": "plugin:counter",
        "session_id": bob.graph_id,
        "creature_id": "alice",
    }
    resp = client.post("/api/extensions/owner/state", json=cross)
    assert (
        resp.status_code == 404 and resp.json()["detail"]["error"] == "owner_not_loaded"
    )
    assert (
        client.post(
            "/api/extensions/owner/call", json={**cross, "action": "add"}
        ).status_code
        == 404
    )
    resp = client.post("/api/extensions/owner/call", json=_ref(bob, action="add"))
    assert resp.json()["detail"]["error"] == "owner_not_loaded"
    alice.agent.plugins.disable("counter")
    resp = client.post("/api/extensions/owner/state", json=_ref(alice))
    assert resp.json()["detail"]["error"] == "owner_not_loaded"

    with client.websocket_connect("/ws/extensions/owner") as ws:
        ws.send_json({"op": "subscribe", "id": "x", **cross})
        message = ws.receive_json()
        assert message["type"] == "error" and message["error"] == "owner_not_loaded"
        ws.send_text("not json")
        assert ws.receive_json()["error"] == "bad_request"
        ws.send_json({"op": "dance", "id": "y"})
        assert ws.receive_json()["error"] == "bad_request"


def test_package_owner_round_trip(ui_package, reset_service):
    ui_package("alpha")
    with TestClient(create_app()) as client:
        ref = {"owner": "package:alpha"}
        assert client.post("/api/extensions/owner/state", json=ref).json()["state"] == {
            "bound": False
        }
        with client.websocket_connect("/ws/extensions/owner") as ws:
            ws.send_json({"op": "subscribe", "id": "p", **ref})
            assert ws.receive_json()["type"] == "subscribed"
            resp = client.post(
                "/api/extensions/owner/call", json={**ref, "action": "bind"}
            )
            assert resp.json()["result"] == {"ok": True}
            assert ws.receive_json()["data"] == {"bound": True}
        bad = client.post(
            "/api/extensions/owner/call", json={**ref, "action": "bad_args"}
        )
        assert bad.status_code == 400
        boom = client.post("/api/extensions/owner/call", json={**ref, "action": "boom"})
        assert (
            boom.status_code == 500 and boom.json()["detail"]["error"] == "owner_failed"
        )
        ghost = client.post(
            "/api/extensions/owner/state", json={"owner": "package:ghost"}
        )
        assert ghost.status_code == 404


def test_multi_user_isolates_user_a_from_user_b(l4_users):
    app, client_a, client_b, user_a, _user_b = l4_users
    engine_a = app.state.engine_pool.get_or_create(user_a)
    alice = client_a.portal.call(engine_a.add_creature, _creature("alice"))
    assert (
        client_a.post("/api/extensions/owner/state", json=_ref(alice)).status_code
        == 200
    )
    resp = client_b.post("/api/extensions/owner/state", json=_ref(alice))
    assert (
        resp.status_code == 404 and resp.json()["detail"]["error"] == "owner_not_loaded"
    )
    assert (
        client_b.post(
            "/api/extensions/owner/call", json=_ref(alice, action="add")
        ).status_code
        == 404
    )
    with client_b.websocket_connect("/ws/extensions/owner") as ws:
        ws.send_json({"op": "subscribe", "id": "x", **_ref(alice)})
        assert ws.receive_json()["error"] == "owner_not_loaded"

    with TestClient(app) as anonymous:
        assert (
            anonymous.post("/api/extensions/owner/state", json=_ref(alice)).status_code
            == 401
        )
        with pytest.raises(WebSocketDisconnect) as info:
            with anonymous.websocket_connect("/ws/extensions/owner") as ws:
                ws.receive_json()
        assert info.value.code == 4401


def test_multi_user_refuses_context_blind_package_owners(ui_package, l4_users):
    app, client_a, client_b, _a, _b = l4_users
    ui_package("blind")
    assert (
        client_a.put("/api/extensions/ui/blind", json={"enabled": True}).status_code
        == 200
    )
    for path, body in (("state", {}), ("call", {"action": "bind"})):
        resp = client_a.post(
            f"/api/extensions/owner/{path}", json={"owner": "package:blind", **body}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "owner_not_multi_user"
    with client_b.websocket_connect("/ws/extensions/owner") as ws:
        ws.send_json({"op": "subscribe", "id": "s", "owner": "package:blind"})
        assert ws.receive_json()["error"] == "owner_not_multi_user"


def test_multi_user_pushes_reach_only_their_target_user(ui_package, l4_users):
    app, client_a, client_b, user_a, user_b = l4_users
    ui_package("aware", ui={"handler": "{module}:UserHandler"})
    assert (
        client_a.put("/api/extensions/ui/aware", json={"enabled": True}).status_code
        == 200
    )
    ref = {"owner": "package:aware"}
    assert client_a.post("/api/extensions/owner/state", json=ref).json()["state"] == {
        "user": user_a
    }
    with (
        client_a.websocket_connect("/ws/extensions/owner") as ws_a,
        client_b.websocket_connect("/ws/extensions/owner") as ws_b,
    ):
        for ws in (ws_a, ws_b):
            ws.send_json({"op": "subscribe", "id": "s", **ref})
            assert ws.receive_json()["type"] == "subscribed"
        # An untargeted push is never delivered under multi-user mode.
        client_a.post("/api/extensions/owner/call", json={**ref, "action": "broadcast"})
        client_b.post("/api/extensions/owner/call", json={**ref, "action": "ping_me"})
        assert ws_b.receive_json()["data"] == {"for": user_b}
        client_a.post("/api/extensions/owner/call", json={**ref, "action": "ping_me"})
        assert ws_a.receive_json()["data"] == {"for": user_a}
        for ws in (ws_a, ws_b):
            ws.send_json({"op": "ping"})
            assert ws.receive_json() == {"type": "pong"}


def test_runtime_owners_of_a_disabled_package_are_unavailable(ui_package, l4_users):
    app, client_a, _client_b, user_a, _b = l4_users
    ui_package("alpha")  # declares plugin "counter" and owns a panel for it
    engine_a = app.state.engine_pool.get_or_create(user_a)
    alice = client_a.portal.call(engine_a.add_creature, _creature("alice"))
    # Multi-user hosts start with package UI disabled.
    for path, body in (("state", {}), ("call", {"action": "add"})):
        resp = client_a.post(
            f"/api/extensions/owner/{path}", json={**_ref(alice), **body}
        )
        assert (
            resp.status_code == 404
            and resp.json()["detail"]["error"] == "owner_disabled"
        )
    with client_a.websocket_connect("/ws/extensions/owner") as ws:
        ws.send_json({"op": "subscribe", "id": "s", **_ref(alice)})
        assert ws.receive_json()["error"] == "owner_disabled"
    assert (
        client_a.put("/api/extensions/ui/alpha", json={"enabled": True}).status_code
        == 200
    )
    assert (
        client_a.post("/api/extensions/owner/state", json=_ref(alice)).status_code
        == 200
    )


def test_outbox_is_bounded_and_drops_the_oldest():
    box = BoundedOutbox(limit=3)
    for i in range(10):
        box.put({"n": i})
    assert len(box) == 3 and box.dropped == 7

    async def drain():
        return [await box.get() for _ in range(4)]

    frames = asyncio.run(drain())
    assert frames[0] == {"type": "overflow", "dropped": 7}
    assert [f["n"] for f in frames[1:]] == [7, 8, 9]
    assert box.dropped == 0
    assert OUTBOX_LIMIT >= 64


async def test_session_ends_when_its_sender_dies():
    class DeadSocket:
        def __init__(self):
            self.inbox = asyncio.Queue()
            self.inbox.put_nowait('{"op": "ping"}')

        async def receive_text(self):
            return await self.inbox.get()

        async def send_text(self, _text):
            raise RuntimeError("peer vanished")

    session = OwnerSocketSession(
        DeadSocket(), service=None, auth_config=AuthConfig(), user=None
    )
    await asyncio.wait_for(session.run(), timeout=2)
    assert session.subscriptions == {}


def test_context_reports_mode_and_admin(ui_package, reset_service):
    ui_package("who", ui={"handler": "{module}:WhoHandler"})
    ref = {"owner": "package:who"}
    with TestClient(create_app()) as client:
        state = client.post("/api/extensions/owner/state", json=ref).json()["state"]
        assert state == {"multi_user": False, "is_admin": True, "user": None}
    app = create_app()
    app.state.auth_config = AuthConfig(multi_user="optional")
    with TestClient(app) as client:
        ui_extensions.set_enabled("who", True)
        state = client.post("/api/extensions/owner/state", json=ref).json()["state"]
        assert state == {"multi_user": True, "is_admin": False, "user": None}
        result = client.post("/api/extensions/owner/call", json={**ref, "action": "x"})
        assert result.json()["result"]["multi_user"] is True
    app = create_app()
    app.state.auth_config = AuthConfig(admin_token="ADMIN")
    with TestClient(app) as client:
        no_token = client.post("/api/extensions/owner/state", json=ref).json()["state"]
        assert no_token["is_admin"] is False
        with_token = client.post(
            "/api/extensions/owner/state", json=ref, headers={"X-Admin-Token": "ADMIN"}
        ).json()["state"]
        assert with_token["is_admin"] is True


def test_forbidden_owner_errors_are_403_and_not_failures(
    ui_package, reset_service, caplog
):
    ui_package("guard", ui={"handler": "{module}:GuardHandler"})
    ref = {"owner": "package:guard"}
    with TestClient(create_app()) as client:
        for path, body in (("state", {}), ("call", {"action": "x"})):
            resp = client.post(f"/api/extensions/owner/{path}", json={**ref, **body})
            assert resp.status_code == 403
            assert resp.json()["detail"] == {
                "error": "owner_forbidden",
                "message": "members only",
            }
        with client.websocket_connect("/ws/extensions/owner") as ws:
            ws.send_json({"op": "subscribe", "id": "g", **ref})
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["error"] == "owner_forbidden"
    assert "UI owner failed" not in caplog.text
