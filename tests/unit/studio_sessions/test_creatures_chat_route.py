"""Regression: ``POST /api/sessions/{sid}/creatures/{cid}/messages/{idx}/edit``
must accept the multimodal payload that the frontend's
``buildMessageParts`` actually emits.

Before the fix, ``MessageEdit.content`` was typed ``str``, so the
frontend's text-only edit (which still arrives as
``[{"type": "text", "text": "..."}]``) hit Pydantic validation and
returned 422.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.sessions_v2 import creatures_chat as route_mod


class _RecordingAgent:
    """Agent that records what edit_and_rerun received."""

    def __init__(self) -> None:
        self.edits: list[tuple] = []

    async def edit_and_rerun(
        self,
        msg_idx: int,
        content,
        *,
        turn_index=None,
        user_position=None,
    ) -> bool:
        self.edits.append((msg_idx, content, turn_index, user_position))
        return True


def _make_client(agent: _RecordingAgent, *, creature_id: str = "alice"):
    app = FastAPI()
    app.include_router(route_mod.router, prefix="/api/sessions")

    fake_creature = SimpleNamespace(
        agent=agent, name=creature_id, creature_id=creature_id, graph_id="g0"
    )

    class _FakeEngine:
        def get_creature(self, cid: str):
            if cid != creature_id:
                raise KeyError(cid)
            return fake_creature

        def list_graphs(self):  # not used but find_creature touches it
            return []

    app.dependency_overrides[get_engine] = lambda: _FakeEngine()
    return TestClient(app)


def test_edit_accepts_plain_string():
    """Legacy callers that send ``content: "..."`` still work."""
    agent = _RecordingAgent()
    client = _make_client(agent)
    resp = client.post(
        "/api/sessions/_/creatures/alice/messages/0/edit",
        json={"content": "hello"},
    )
    assert resp.status_code == 200, resp.text
    assert agent.edits == [(0, "hello", None, None)]


def test_edit_accepts_multimodal_list():
    """Frontend-emitted content-part list must validate.

    This is the exact body shape that produced the original 422:
    a non-empty ``content`` list where every part is a typed dict.
    """
    agent = _RecordingAgent()
    client = _make_client(agent)
    body = {
        "content": [{"type": "text", "text": "edited"}],
        "turn_index": 2,
        "user_position": 1,
    }
    resp = client.post(
        "/api/sessions/_/creatures/alice/messages/0/edit",
        json=body,
    )
    assert resp.status_code == 200, resp.text
    # The route flattens Pydantic models back to plain dicts before
    # forwarding so the agent sees a normal list[dict] payload.
    assert len(agent.edits) == 1
    msg_idx, content, ti, up = agent.edits[0]
    assert msg_idx == 0
    assert ti == 2 and up == 1
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "edited"


def test_edit_accepts_image_part():
    """Multimodal image edits must validate too."""
    agent = _RecordingAgent()
    client = _make_client(agent)
    body = {
        "content": [
            {"type": "text", "text": "look"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc", "detail": "low"},
            },
        ]
    }
    resp = client.post(
        "/api/sessions/_/creatures/alice/messages/3/edit",
        json=body,
    )
    assert resp.status_code == 200, resp.text


def test_edit_rejects_empty_body_with_clear_error():
    """Sanity: a body missing ``content`` is still a 422 (Pydantic)."""
    agent = _RecordingAgent()
    client = _make_client(agent)
    resp = client.post(
        "/api/sessions/_/creatures/alice/messages/0/edit",
        json={"turn_index": 1},
    )
    assert resp.status_code == 422
