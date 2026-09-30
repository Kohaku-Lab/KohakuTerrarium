"""E2E journey — creatures on the Anthropic provider with sampling settings.

Reproduces the report "the new Anthropic SDK moved temperature into extra_body
and chat fails": a user chats over the real HTTP + WebSocket API with a creature
whose provider carries sampling settings. A real ``AnthropicProvider`` and a real
SDK client run; only the network (an in-memory ``httpx2`` transport) is replaced.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from tests.helpers.anthropic_stream import ANSWER, anthropic_provider

pytestmark = pytest.mark.timeout(60)

SAMPLING = {"temperature": 0.3, "extra_body": {"top_p": 0.9, "top_k": 5}}
MODELS = {"older": "claude-sonnet-4-6", "newer": "claude-opus-4-8"}
CREATURE = """\
name: {name}
system_prompt: "You are a deterministic e2e-test creature."
tool_format: native
input:
  type: none
output:
  type: stdout
"""


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list[dict]]:
    """Request bodies that reached the fake API, keyed by creature name."""
    seen: dict[str, list[dict[str, Any]]] = {name: [] for name in MODELS}

    def _create(config, selector=None):
        return anthropic_provider(MODELS[config.name], seen[config.name], **SAMPLING)

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _create)
    return seen


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wire: dict[str, list[dict]]
) -> Iterator[TestClient]:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    engine = Terrarium(session_dir=str(session_dir))
    set_service(LocalTerrariumService(engine))
    with TestClient(create_app()) as test_client:
        yield test_client
    set_service(None)


def _chat(client: TestClient, config_dir: Path, name: str) -> str:
    resp = client.post(
        "/api/sessions/active/agents",
        json={"config_path": str(config_dir), "name": name},
    )
    assert resp.status_code == 200
    session_id, creature_id = resp.json()["session_id"], resp.json()["agent_id"]
    chunks: list[str] = []
    with client.websocket_connect(
        f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
    ) as ws:
        ws.receive_json()
        ws.send_json({"type": "input", "content": "what is the answer?"})
        while True:
            frame = ws.receive_json()
            assert frame.get("type") != "error", frame
            assert frame.get("activity_type") != "processing_error", frame
            if frame.get("type") == "text":
                chunks.append(frame["content"])
            if frame.get("type") == "idle":
                break
    return "".join(chunks)


class TestAnthropicSamplingJourney:
    def test_chat_works_and_sampling_follows_the_model(
        self, client: TestClient, tmp_path: Path, wire: dict[str, list[dict]]
    ) -> None:
        for name in MODELS:
            config_dir = tmp_path / name
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                CREATURE.format(name=name), encoding="utf-8"
            )
            assert _chat(client, config_dir, name) == ANSWER

        older = wire["older"][0]
        assert older["model"] == "claude-sonnet-4-6"
        assert (older["temperature"], older["top_p"], older["top_k"]) == (0.3, 0.9, 5)

        newer = wire["newer"][0]
        assert newer["model"] == "claude-opus-4-8"
        assert not {"temperature", "top_p", "top_k"} & set(newer)
        assert len(wire["older"]) == len(wire["newer"]) == 1
