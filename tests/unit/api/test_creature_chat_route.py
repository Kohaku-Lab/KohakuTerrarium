"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures_chat`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import creatures_chat as chat_mod
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice"):
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id="g",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


class _FakeService:
    def __init__(
        self,
        *,
        creatures=None,
        chat_chunks=None,
        regen_returns=None,
        edit_returns=True,
        history_returns=None,
        branches_returns=None,
        raise_on=None,
    ):
        self._creatures = creatures or [_info()]
        self._chunks = chat_chunks if chat_chunks is not None else ["hi", " ", "there"]
        self._regen = regen_returns
        self._edit_returns = edit_returns
        self._history = history_returns or {"messages": []}
        self._branches = branches_returns or [{"t": 1}]
        self._raise = raise_on or {}
        self.engine = object()

    async def list_creatures(self):
        return tuple(self._creatures)

    def chat(self, cid, message):
        if "chat" in self._raise:

            async def boom():
                raise self._raise["chat"]
                yield  # pragma: no cover

            return boom()

        async def gen():
            for c in self._chunks:
                yield c

        return gen()

    async def regenerate(self, cid, *, turn_index=None, branch_view=None):
        if "regenerate" in self._raise:
            raise self._raise["regenerate"]
        return self._regen

    async def edit_message(self, cid, idx, content, **kw):
        if "edit_message" in self._raise:
            raise self._raise["edit_message"]
        return self._edit_returns

    async def rewind(self, cid, idx):
        if "rewind" in self._raise:
            raise self._raise["rewind"]

    async def chat_history(self, cid):
        if "chat_history" in self._raise:
            raise self._raise["chat_history"]
        return self._history

    async def chat_branches(self, cid):
        if "chat_branches" in self._raise:
            raise self._raise["chat_branches"]
        return self._branches


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(chat_mod.router, prefix="/sessions")
    return TestClient(app)


# ── chat ───────────────────────────────────────────────────────


class TestChat:
    def test_message_field(self):
        svc = _FakeService(chat_chunks=["a", "b"])
        client = _client(svc)
        resp = client.post(
            "/sessions/g/creatures/alice/chat",
            json={"message": "hi"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": "ab"}

    def test_content_field(self):
        svc = _FakeService(chat_chunks=["x"])
        client = _client(svc)
        resp = client.post(
            "/sessions/g/creatures/alice/chat",
            json={
                "content": [{"type": "text", "text": "hi"}],
            },
        )
        assert resp.status_code == 200

    def test_unknown_creature(self):
        svc = _FakeService(creatures=[])
        client = _client(svc)
        resp = client.post("/sessions/g/creatures/ghost/chat", json={"message": "x"})
        assert resp.status_code == 404

    def test_chat_keyerror_returns_404(self):
        svc = _FakeService(raise_on={"chat": KeyError("not hosted")})
        client = _client(svc)
        resp = client.post("/sessions/g/creatures/alice/chat", json={"message": "x"})
        assert resp.status_code == 404


# ── regenerate ────────────────────────────────────────────────


class TestRegenerate:
    def test_default_body(self):
        client = _client(_FakeService())
        resp = client.post("/sessions/g/creatures/alice/regenerate", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "regenerating"

    def test_with_turn_index(self):
        client = _client(_FakeService())
        resp = client.post(
            "/sessions/g/creatures/alice/regenerate",
            json={"turn_index": 3},
        )
        body = resp.json()
        assert body["turn_index"] == 3

    def test_unknown_creature(self):
        client = _client(_FakeService(creatures=[]))
        resp = client.post("/sessions/g/creatures/ghost/regenerate", json={})
        assert resp.status_code == 404

    def test_keyerror_returns_404(self):
        svc = _FakeService(raise_on={"regenerate": KeyError("no")})
        client = _client(svc)
        resp = client.post("/sessions/g/creatures/alice/regenerate", json={})
        assert resp.status_code == 404


# ── edit_message ──────────────────────────────────────────────


class TestEditMessage:
    def test_success(self):
        client = _client(_FakeService(edit_returns=True))
        resp = client.post(
            "/sessions/g/creatures/alice/messages/0/edit",
            json={"content": "new text"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "edited"

    def test_with_content_list(self):
        client = _client(_FakeService(edit_returns=True))
        resp = client.post(
            "/sessions/g/creatures/alice/messages/0/edit",
            json={"content": [{"type": "text", "text": "x"}]},
        )
        assert resp.status_code == 200

    def test_not_edited(self):
        client = _client(_FakeService(edit_returns=False))
        resp = client.post(
            "/sessions/g/creatures/alice/messages/0/edit",
            json={"content": "x"},
        )
        assert resp.status_code == 400

    def test_keyerror(self):
        svc = _FakeService(raise_on={"edit_message": KeyError("no")})
        client = _client(svc)
        resp = client.post(
            "/sessions/g/creatures/alice/messages/0/edit",
            json={"content": "x"},
        )
        assert resp.status_code == 404


# ── rewind ────────────────────────────────────────────────────


class TestRewind:
    def test_success(self):
        client = _client(_FakeService())
        resp = client.post("/sessions/g/creatures/alice/messages/0/rewind")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rewound"

    def test_keyerror(self):
        svc = _FakeService(raise_on={"rewind": KeyError("no")})
        client = _client(svc)
        resp = client.post("/sessions/g/creatures/alice/messages/0/rewind")
        assert resp.status_code == 404


# ── history / branches ────────────────────────────────────────


class TestHistoryBranches:
    def test_history_creature(self):
        svc = _FakeService(history_returns={"messages": [{"role": "user"}]})
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/alice/history")
        assert resp.status_code == 200
        assert resp.json()["messages"][0]["role"] == "user"

    def test_history_channel_route(self, monkeypatch):
        # `ch:<name>` prefix prefers the host-engine-backed
        # _channel_history when it returns non-empty events.
        captured = []

        def fake_ch(engine, sid, name):
            captured.append((sid, name))
            return {
                "channel": name,
                "events": [{"type": "channel_message", "content": "hi"}],
            }

        monkeypatch.setattr(chat_mod, "_channel_history", fake_ch)
        client = _client(_FakeService())
        resp = client.get("/sessions/g/creatures/ch:chat-ch/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["channel"] == "chat-ch"
        assert body["events"][0]["content"] == "hi"
        assert captured == [("g", "chat-ch")]

    def test_history_channel_route_falls_back_to_service(self, monkeypatch):
        """CF-9: when the host-engine ``_channel_history`` has nothing
        (lab-host mode / cluster channel without host-attached store),
        the route MUST delegate to ``service.channel_history`` so the
        merged cluster history surfaces. Pre-CF-9 the route returned
        an empty list and the channel tab rendered blank."""
        # Force the _channel_history fallback path: simulate lab-host
        # by patching host_engine_or_none to return None.
        monkeypatch.setattr(chat_mod, "host_engine_or_none", lambda s: None)

        async def fake_channel_history(self, gid, name):
            return [
                {"sender": "alpha", "content": "from-w1", "ts": 1.0},
                {"sender": "bravo", "content": "from-w2", "ts": 2.0},
            ]

        # Attach an async channel_history method on the fake service.
        svc = _FakeService()
        svc.channel_history = fake_channel_history.__get__(
            svc, type(svc)
        )  # noqa: SLF001
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/ch:chat-ch/history")
        assert resp.status_code == 200
        body = resp.json()
        # The cluster-routed history surfaced in events.
        contents = [e["content"] for e in body["events"]]
        assert "from-w1" in contents
        assert "from-w2" in contents

    def test_history_keyerror(self):
        svc = _FakeService(raise_on={"chat_history": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/alice/history")
        assert resp.status_code == 404

    def test_branches(self):
        svc = _FakeService(branches_returns=[{"t": 1}, {"t": 2}])
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/alice/branches")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_branches_keyerror(self):
        svc = _FakeService(raise_on={"chat_branches": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/alice/branches")
        assert resp.status_code == 404
