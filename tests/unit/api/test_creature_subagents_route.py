"""Unit tests for
:mod:`kohakuterrarium.api.routes.sessions_v2.creatures_subagents`.

The route resolves the creature via the service then delegates the
sub-agent read / interactive-send to the studio accessor; these tests
stub the accessor (patched on the route module) and pin the routing +
error-status mapping.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import creatures_subagents as sub_mod
from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice") -> CreatureInfo:
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
    def __init__(self, *, creatures=None):
        self._creatures = creatures or [_info()]
        self.engine = object()

    async def list_creatures(self):
        return tuple(self._creatures)


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(sub_mod.router, prefix="/sessions")
    return TestClient(app)


# ── read conversation ─────────────────────────────────────────


class TestReadConversation:
    def test_read_success(self, monkeypatch):
        captured: dict = {}

        def fake_read(service, session_id, creature_id, *, job_id, name, run):
            captured.update(
                session_id=session_id,
                creature_id=creature_id,
                job_id=job_id,
                name=name,
                run=run,
            )
            return {"name": name or "", "messages": [{"role": "user"}], "live": True}

        monkeypatch.setattr(sub_mod, "read_subagent_conversation", fake_read)
        client = _client(_FakeService())
        resp = client.get(
            "/sessions/g/creatures/alice/subagents/conversation",
            params={"job_id": "agent_explore_ab12"},
        )
        assert resp.status_code == 200
        assert resp.json()["messages"][0]["role"] == "user"
        # The route resolves the display name to the canonical creature id.
        assert captured["creature_id"] == "cid"
        assert captured["job_id"] == "agent_explore_ab12"

    def test_read_saved_by_name_and_run(self, monkeypatch):
        captured: dict = {}

        def fake_read(service, session_id, creature_id, *, job_id, name, run):
            captured.update(name=name, run=run, job_id=job_id)
            return {"name": name, "run": run, "messages": []}

        monkeypatch.setattr(sub_mod, "read_subagent_conversation", fake_read)
        client = _client(_FakeService())
        resp = client.get(
            "/sessions/g/creatures/alice/subagents/conversation",
            params={"name": "explore", "run": 2},
        )
        assert resp.status_code == 200
        assert captured == {"name": "explore", "run": 2, "job_id": None}

    def test_read_unknown_run_is_404(self, monkeypatch):
        def fake_read(*a, **k):
            raise NotFoundError("live sub-agent job 'x' not found")

        monkeypatch.setattr(sub_mod, "read_subagent_conversation", fake_read)
        client = _client(_FakeService())
        resp = client.get(
            "/sessions/g/creatures/alice/subagents/conversation",
            params={"job_id": "x"},
        )
        assert resp.status_code == 404

    def test_read_missing_identifier_is_400(self, monkeypatch):
        def fake_read(*a, **k):
            raise InvalidRequestError("provide job_id, or name (with optional run)")

        monkeypatch.setattr(sub_mod, "read_subagent_conversation", fake_read)
        client = _client(_FakeService())
        resp = client.get("/sessions/g/creatures/alice/subagents/conversation")
        assert resp.status_code == 400

    def test_read_unknown_creature_is_404(self, monkeypatch):
        # resolve_creature_id 404s before the accessor is ever called.
        called = {"n": 0}

        def fake_read(*a, **k):
            called["n"] += 1
            return {}

        monkeypatch.setattr(sub_mod, "read_subagent_conversation", fake_read)
        client = _client(_FakeService(creatures=[]))
        resp = client.get(
            "/sessions/g/creatures/ghost/subagents/conversation",
            params={"job_id": "x"},
        )
        assert resp.status_code == 404
        assert called["n"] == 0


# ── send ──────────────────────────────────────────────────────


class TestSend:
    def test_send_success_passes_job_id(self, monkeypatch):
        captured: dict = {}

        async def fake_send(service, session_id, creature_id, name, content, *, job_id):
            captured.update(
                name=name, content=content, creature_id=creature_id, job_id=job_id
            )
            return {"status": "sent", "name": name, "job_id": job_id}

        monkeypatch.setattr(sub_mod, "send_to_subagent", fake_send)
        client = _client(_FakeService())
        resp = client.post(
            "/sessions/g/creatures/alice/subagents/writer/send",
            json={"content": "keep going", "job_id": "agent_writer_a1b2c3d4"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "sent",
            "name": "writer",
            "job_id": "agent_writer_a1b2c3d4",
        }
        # The route forwards the body's job_id for precise live targeting.
        assert captured == {
            "name": "writer",
            "content": "keep going",
            "creature_id": "cid",
            "job_id": "agent_writer_a1b2c3d4",
        }

    def test_send_without_job_id_falls_back_to_name(self, monkeypatch):
        seen: dict = {}

        async def fake_send(service, session_id, creature_id, name, content, *, job_id):
            seen.update(content=content, job_id=job_id)
            return {"status": "sent", "name": name, "job_id": job_id}

        monkeypatch.setattr(sub_mod, "send_to_subagent", fake_send)
        client = _client(_FakeService())
        resp = client.post(
            "/sessions/g/creatures/alice/subagents/writer/send",
            json={"message": "via message field"},
        )
        assert resp.status_code == 200
        assert seen == {"content": "via message field", "job_id": None}

    def test_send_empty_content_is_400(self, monkeypatch):
        # Whitespace-only content is rejected before dispatch — never
        # injected as an empty turn.
        async def fake_send(*a, **k):  # pragma: no cover - must not run
            raise AssertionError("send should not be dispatched for empty content")

        monkeypatch.setattr(sub_mod, "send_to_subagent", fake_send)
        client = _client(_FakeService())
        resp = client.post(
            "/sessions/g/creatures/alice/subagents/writer/send",
            json={"content": "   "},
        )
        assert resp.status_code == 400

    def test_send_with_no_live_target_is_409(self, monkeypatch):
        async def fake_send(*a, **k):
            raise ConflictError("no live sub-agent to receive the message")

        monkeypatch.setattr(sub_mod, "send_to_subagent", fake_send)
        client = _client(_FakeService())
        resp = client.post(
            "/sessions/g/creatures/alice/subagents/writer/send",
            json={"content": "hi"},
        )
        assert resp.status_code == 409

    def test_send_unknown_creature_is_404(self, monkeypatch):
        async def fake_send(*a, **k):  # pragma: no cover - must not run
            return {}

        monkeypatch.setattr(sub_mod, "send_to_subagent", fake_send)
        client = _client(_FakeService(creatures=[]))
        resp = client.post(
            "/sessions/g/creatures/ghost/subagents/writer/send",
            json={"content": "hi"},
        )
        assert resp.status_code == 404
