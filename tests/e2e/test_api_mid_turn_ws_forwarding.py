"""E2E — WS forwarding survives a mid-turn user_input injection.

User-reported bug (2026-05-28): with a real LLM (openrouter/gemini)
using native tool calls, after a mid-turn ``user_input`` is buffered +
drained, the WebSocket stream stops delivering frames mid-turn.
Backend logs show the drain ran and subsequent rounds executed to
completion, but the FE never receives ``user_input_injected`` nor any
of the round-2+ activities. Hard-refresh fixes it; tab switch does
not — proving the WS sink itself is the breakage, not FE state.

Production uses **native** tool calls (OpenAI ``tool_calls`` field).
Text-format calls (``[/tool]args[tool/]``) take a different path
through ``_collect_and_push_feedback``. This test pins **both** modes
so a fix that only repairs one path is caught.

Each test:

1. Opens a real ``create_app`` FastAPI app over the TestClient WS.
2. Creates a creature whose ``tool_format`` matches the parameter.
3. Drives a multi-round turn (round 1 → tool → round 2 → tool → round
   3 → final text).
4. Injects a second ``user_input`` WHILE round 1's LLM is still
   streaming.
5. Asserts the WS keeps forwarding frames PAST the drain point — the
   ``user_input_injected`` activity AND all round-2+ frames must
   reach the client.
"""

from collections.abc import Iterator
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

pytestmark = pytest.mark.timeout(60)


# Round-1 text scripts (the [/scratchpad]...[scratchpad/] block is the
# bracket-format tool call the parser detects; text mode only).
_ROUND1_BRACKET = (
    "thinking about your request "
    + "." * 30
    + "[/scratchpad]@@action=set\n@@key=k1\n@@value=v1\n[scratchpad/]"
)
_ROUND2_BRACKET = "[/scratchpad]@@action=set\n@@key=k2\n@@value=v2\n[scratchpad/]"
_ROUND3_TEXT = "Done with both keys."

# Native-mode equivalents: the LLM yields a thinking blurb and signals
# the tool call via ``last_tool_calls`` instead of inline brackets.
_ROUND1_NATIVE_TEXT = "thinking about your request " + "." * 30
_ROUND2_NATIVE_TEXT = ""


class _NativeScriptedLLM(ScriptedLLM):
    """ScriptedLLM that ALSO emits native ``last_tool_calls`` between
    yields, mirroring how a real provider populates the attribute the
    controller reads in native mode.
    """

    def __init__(self, script_with_calls: list[tuple[ScriptEntry, list]]):
        text_only = [entry for entry, _ in script_with_calls]
        super().__init__(text_only)
        self._calls_per_round = [calls for _, calls in script_with_calls]
        self.last_tool_calls: list[NativeToolCall] = []
        self.last_assistant_extra_fields: dict[str, Any] = {}
        self.last_usage: dict[str, int] = {}

    async def chat(self, messages, **kwargs):  # type: ignore[override]
        idx = self.call_count
        # Reset before this call so previous round's calls don't leak
        # if the entry has none.
        self.last_tool_calls = []
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        if idx < len(self._calls_per_round):
            self.last_tool_calls = list(self._calls_per_round[idx])


_BRACKET_CONFIG = """\
name: alice
system_prompt: "You are a deterministic e2e-test creature."
tool_format: bracket
input:
  type: none
output:
  type: stdout
tools:
  - name: scratchpad
    type: builtin
"""

_NATIVE_CONFIG = """\
name: alice
system_prompt: "You are a deterministic e2e-test creature."
controller:
  tool_format: native
input:
  type: none
output:
  type: stdout
tools:
  - name: scratchpad
    type: builtin
"""


@pytest.fixture
def bracket_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Bracket-format script (text-mode tool calls)."""
    llm = ScriptedLLM(
        [
            ScriptEntry(
                _ROUND1_BRACKET,
                match="run multi-round tools",
                chunk_size=1,
                delay_per_chunk=0.03,
            ),
            ScriptEntry(_ROUND2_BRACKET),
            ScriptEntry(_ROUND3_TEXT),
        ]
    )
    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", lambda *a, **k: llm)
    monkeypatch.setattr(_agent_init, "create_llm_provider", lambda *a, **k: llm)
    return llm


@pytest.fixture
def native_llm(monkeypatch: pytest.MonkeyPatch) -> _NativeScriptedLLM:
    """Native-format script — mirrors the openrouter/gemini production
    code path where tool calls arrive via ``last_tool_calls``."""
    llm = _NativeScriptedLLM(
        [
            (
                ScriptEntry(
                    _ROUND1_NATIVE_TEXT,
                    match="run multi-round tools",
                    chunk_size=1,
                    delay_per_chunk=0.03,
                ),
                [
                    NativeToolCall(
                        id="call_1",
                        name="scratchpad",
                        arguments='{"action":"set","key":"k1","value":"v1"}',
                    )
                ],
            ),
            (
                ScriptEntry(_ROUND2_NATIVE_TEXT),
                [
                    NativeToolCall(
                        id="call_2",
                        name="scratchpad",
                        arguments='{"action":"set","key":"k2","value":"v2"}',
                    )
                ],
            ),
            (ScriptEntry(_ROUND3_TEXT), []),
        ]
    )
    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", lambda *a, **k: llm)
    monkeypatch.setattr(_agent_init, "create_llm_provider", lambda *a, **k: llm)
    return llm


@pytest.fixture
def bracket_creature_dir(tmp_path: Path) -> Path:
    cdir = tmp_path / "alice_b"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(_BRACKET_CONFIG, encoding="utf-8")
    return cdir


@pytest.fixture
def native_creature_dir(tmp_path: Path) -> Path:
    cdir = tmp_path / "alice_n"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(_NATIVE_CONFIG, encoding="utf-8")
    return cdir


def _make_client(monkeypatch, tmp_path) -> Iterator[TestClient]:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    set_service(None)


@pytest.fixture
def bracket_client(monkeypatch, tmp_path, bracket_llm):
    yield from _make_client(monkeypatch, tmp_path)


@pytest.fixture
def native_client(monkeypatch, tmp_path, native_llm):
    yield from _make_client(monkeypatch, tmp_path)


def _drain_frames_until(
    ws, *, terminal_type: str, timeout_s: float = 15.0
) -> list[dict]:
    """Receive WS frames until ``terminal_type`` arrives or the timeout
    elapses. Returns every collected frame including the terminal one.

    A background pump thread lets us cap how long we spend blocking on
    ``receive_json``. If the bug under test (WS sink stops forwarding)
    is live, the timeout fires with the last-10-frame context instead
    of hanging the whole pytest run.
    """
    import time

    frames: list[dict] = []
    deadline = time.monotonic() + timeout_s
    inbox: Queue = Queue()

    def _pump() -> None:
        try:
            while True:
                frame = ws.receive_json()
                inbox.put(frame)
                if frame.get("type") == terminal_type:
                    return
        except Exception as exc:
            inbox.put({"_pump_error": str(exc)})

    pump_thread = Thread(target=_pump, daemon=True)
    pump_thread.start()

    while time.monotonic() < deadline:
        try:
            frame = inbox.get(timeout=0.2)
        except Empty:
            continue
        if "_pump_error" in frame:
            raise AssertionError(f"WS pump raised: {frame['_pump_error']}")
        frames.append(frame)
        if frame.get("type") == terminal_type:
            return frames

    raise AssertionError(
        f"Timed out waiting for {terminal_type!r} after {timeout_s}s. "
        f"Last 10 frames: {frames[-10:]}"
    )


def _run_mid_turn_journey(client: TestClient, creature_dir: Path) -> list[dict]:
    """Common drive: create creature, open WS, send first input,
    inject second input mid-stream, collect every frame until idle."""
    resp = client.post(
        "/api/sessions/active/agents",
        json={"config_path": str(creature_dir)},
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    session_id = created["session_id"]
    creature_id = created["agent_id"]

    ws_url = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
    with client.websocket_connect(ws_url) as ws:
        info = ws.receive_json()
        assert info["activity_type"] == "session_info"

        ws.send_json({"type": "input", "content": "please run multi-round tools"})

        preamble: list[dict] = []
        while True:
            frame = ws.receive_json()
            preamble.append(frame)
            if frame.get("type") == "processing_start":
                break

        # Inject mid-turn — the round-1 LLM is still streaming (slow
        # chunks), so the processing lock is held and this submission
        # MUST be buffered + drained later.
        ws.send_json({"type": "input", "content": "mid-turn message"})

        tail = _drain_frames_until(ws, terminal_type="idle", timeout_s=15.0)

    return preamble + tail


def _assert_mid_turn_ws_invariants(
    frames: list[dict], llm_call_count: int, *, expect_round3_text: bool = True
) -> None:
    """Assert the WS sink kept forwarding past the drain point."""
    # 1. Server-side echo of the mid-turn submission arrived.
    user_input_echos = [
        f
        for f in frames
        if f.get("type") == "user_input"
        and (
            f.get("content") == "mid-turn message"
            or (
                isinstance(f.get("content"), list)
                and any(
                    isinstance(p, dict) and p.get("text") == "mid-turn message"
                    for p in f["content"]
                )
            )
        )
    ]
    assert user_input_echos, (
        "Second-input echo missing — backend never accepted the mid-turn frame. "
        f"frames={frames!r}"
    )

    # 2. The drain emitted ``user_input_injected`` carrying the buffered
    #    content. This is the SPECIFIC activity the FE listens on to
    #    clear the queued banner.
    injected = [
        f
        for f in frames
        if f.get("type") == "activity"
        and f.get("activity_type") == "user_input_injected"
    ]
    assert injected, (
        "user_input_injected activity missing — drain ran but WS sink "
        "never delivered the frame. "
        f"last 15 frames: {frames[-15:]!r}"
    )
    # 2a. JSON-serializability regression guard. The user lost 8 hours
    #     to ``create_user_input_event`` returning ``[TextPart(...)]``
    #     dataclasses through the drain into the WS metadata. TestClient
    #     doesn't actually run ``json.dumps`` on send (it passes dicts
    #     through), so the e2e flow above wouldn't catch a TextPart
    #     regression. Explicitly check that EVERY collected activity
    #     frame survives a real ``json.dumps`` round-trip — this matches
    #     what ``ws.send_json`` does in production (uvicorn).
    import json

    for f in frames:
        if f.get("type") != "activity":
            continue
        try:
            json.dumps(f)
        except TypeError as exc:
            raise AssertionError(
                f"Activity frame is NOT JSON-serializable — would kill "
                f"_forward_queue in production. activity_type="
                f"{f.get('activity_type')!r} error={exc}"
            )
    inj_content = injected[0].get("content")
    text = ""
    if isinstance(inj_content, str):
        text = inj_content
    elif isinstance(inj_content, list):
        text = "".join(
            p.get("text", "")
            for p in inj_content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    assert "mid-turn message" in text, (
        f"user_input_injected payload did not echo the buffered text: "
        f"{injected[0]!r}"
    )

    # 3. THE CRITICAL CHECK — frames KEPT FLOWING after the injection.
    #    At least round 1 and round 2 must have emitted tool_start +
    #    tool_done activities. If the WS sink died after the drain, the
    #    round-2 frames never reach the client.
    tool_starts = [
        f
        for f in frames
        if f.get("type") == "activity" and f.get("activity_type") == "tool_start"
    ]
    tool_dones = [
        f
        for f in frames
        if f.get("type") == "activity" and f.get("activity_type") == "tool_done"
    ]
    assert len(tool_starts) >= 2, (
        f"Expected at least 2 tool_start frames (rounds 1+2). "
        f"Got {len(tool_starts)}. Round-2 frames lost to the WS sink. "
        f"Activities seen: "
        f"{[f.get('activity_type') for f in frames if f.get('type') == 'activity']!r}"
    )
    assert len(tool_dones) >= 2, (
        f"Expected at least 2 tool_done frames (rounds 1+2). "
        f"Got {len(tool_dones)}. Round-2 tool_done lost to the WS sink."
    )

    # 4. The terminal ``idle`` arrived.
    assert frames[-1].get("type") == "idle"

    # 5. Round-3 final text reached the WS.
    if expect_round3_text:
        text_chunks = [f.get("content", "") for f in frames if f.get("type") == "text"]
        combined = "".join(text_chunks)
        assert (
            "Done with both keys" in combined
        ), f"Round-3 final text missing from WS stream: {combined!r}"

    # 6. LLM ran exactly 3 times (rounds 1+2+3).
    assert llm_call_count == 3, f"Expected 3 LLM calls, got {llm_call_count}"


class TestMidTurnWSForwarding:
    """Pin that the WS forward task survives a mid-turn drain in BOTH
    bracket and native tool-call modes."""

    def test_bracket_mode_ws_keeps_streaming_after_mid_turn_injection(
        self,
        bracket_client: TestClient,
        bracket_creature_dir: Path,
        bracket_llm: ScriptedLLM,
    ) -> None:
        frames = _run_mid_turn_journey(bracket_client, bracket_creature_dir)
        _assert_mid_turn_ws_invariants(frames, bracket_llm.call_count)

    def test_native_mode_ws_keeps_streaming_after_mid_turn_injection(
        self,
        native_client: TestClient,
        native_creature_dir: Path,
        native_llm: _NativeScriptedLLM,
    ) -> None:
        """Production (openrouter/gemini) exercises this path."""
        frames = _run_mid_turn_journey(native_client, native_creature_dir)
        _assert_mid_turn_ws_invariants(frames, native_llm.call_count)
