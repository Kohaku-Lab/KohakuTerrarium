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

    async def regenerate(
        self, cid, *, turn_index=None, branch_view=None, request_id=None
    ):
        if "regenerate" in self._raise:
            raise self._raise["regenerate"]
        return self._regen or {
            "status": "completed",
            "request_id": request_id or "generated",
            "turn_index": turn_index or 0,
            "branch_id": 0,
            "parent_branch_path": [],
        }

    async def edit_message(self, cid, idx, content, **kw):
        if "edit_message" in self._raise:
            raise self._raise["edit_message"]
        if self._edit_returns is False:
            raise ValueError("invalid edit target")
        if self._edit_returns not in (None, True):
            return self._edit_returns
        return {
            "status": "completed",
            "request_id": kw.get("request_id") or "generated",
            "turn_index": kw.get("turn_index") or 0,
            "branch_id": 0,
            "parent_branch_path": [],
        }

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
        assert resp.json()["status"] == "completed"

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
        assert resp.json()["status"] == "completed"

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
        assert resp.status_code == 409

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

    def test_history_channel_route_uses_service(self):
        async def fake_channel_history(gid, name):
            assert (gid, name) == ("g", "chat-ch")
            return [
                {"sender": "alpha", "content": "from-w1", "timestamp": 1.0},
                {"sender": "bravo", "content": "from-w2", "timestamp": 2.0},
            ]

        svc = _FakeService()
        svc.channel_history = fake_channel_history
        client = _client(svc)
        resp = client.get("/sessions/g/creatures/ch:chat-ch/history")
        assert resp.status_code == 200
        body = resp.json()
        assert [event["content"] for event in body["events"]] == [
            "from-w1",
            "from-w2",
        ]

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
