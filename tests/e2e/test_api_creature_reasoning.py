"""E2E journey — a creature on a provider that streams dual-form reasoning.

Reproduces the "Thinking · reasoning_content / Thinking · reasoning_details"
accordion flood as the user hits it: chat over the real HTTP + WebSocket API,
then reopen the conversation history.

The seam is the HTTP transport under a real ``OpenAIProvider`` (one layer below
the usual ``ScriptedLLM`` seam), because the segments under test are built inside
the provider's stream parser.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from tests.helpers.reasoning_stream import (
    ANSWER,
    REASONING_PIECES,
    dual_form_provider,
    reasoning_segments,
)

pytestmark = pytest.mark.timeout(60)

_CREATURE_CONFIG = """\
name: thinker
system_prompt: "You are a deterministic e2e-test creature."
tool_format: native
input:
  type: none
output:
  type: stdout
"""


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    provider = dual_form_provider()

    def _create(config, selector=None):
        return provider

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _create)

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    engine = Terrarium(session_dir=str(session_dir))
    set_service(LocalTerrariumService(engine))
    with TestClient(create_app()) as test_client:
        yield test_client
    set_service(None)


class TestDualFormReasoningJourney:
    def test_reasoning_reaches_the_ui_as_one_segment_per_form(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        cdir = tmp_path / "thinker"
        cdir.mkdir()
        (cdir / "config.yaml").write_text(_CREATURE_CONFIG, encoding="utf-8")

        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(cdir), "name": "thinker"},
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        creature_id = resp.json()["agent_id"]

        live_segments: list[dict] = []
        answer_chunks: list[str] = []
        with client.websocket_connect(
            f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
        ) as ws:
            ws.receive_json()  # session_info
            ws.send_json({"type": "input", "content": "what is the answer?"})
            while True:
                frame = ws.receive_json()
                if frame.get("type") == "text":
                    answer_chunks.append(frame["content"])
                if frame.get("_kt_assistant_segments"):
                    live_segments = frame["_kt_assistant_segments"]
                if frame.get("type") == "idle":
                    break

        assert "".join(answer_chunks) == ANSWER

        history = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/history"
        ).json()
        persisted = [
            e["_kt_assistant_segments"]
            for e in history["events"]
            if e.get("type") == "assistant_reasoning"
        ]
        assert persisted, "no assistant_reasoning event was recorded"

        expected_text = "".join(REASONING_PIECES)
        for label, segments in (
            ("live frame", live_segments),
            ("history", persisted[0]),
        ):
            reasoning = reasoning_segments(segments)
            assert [s["source"] for s in reasoning] == [
                "reasoning_content",
                "reasoning_details",
            ], f"{label}: one accordion per delta, not per form: {reasoning!r}"
            assert [s["text"] for s in reasoning] == [expected_text, expected_text]
