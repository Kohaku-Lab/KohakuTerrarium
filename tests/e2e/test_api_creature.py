"""E2E journey — HTTP+WS single-creature usage.

This is the *whole project* exercised the way the real Vue frontend
drives a creature: a real :func:`create_app` FastAPI app, a real
:class:`LocalTerrariumService` over a real :class:`Terrarium`
installed via :func:`api.deps.set_service`, driven through
``fastapi.testclient.TestClient`` over both HTTP and WebSocket.

The ONLY seam is the LLM — both ``create_llm_provider`` bind points
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to
a deterministic :class:`ScriptedLLM`. Engine, session stores, on-disk
``.kohakutr`` persistence, the WS attach loop, tool execution, branch
bookkeeping — everything else runs for real.

Each ``TestApiCreatureJourney`` method is ONE fat journey: a single
function that runs an entire user session in sequence, asserting the
observable state at every milestone. The call sequence mirrors
``src/kohakuterrarium-frontend/src/utils/api.js``:

* :meth:`test_chat_and_settings_journey` — ``agentAPI.create`` →
  ``sessionAPI.listActive`` / ``getActive`` → the
  ``/ws/sessions/.../chat`` attach (multi-turn stream + a tool-call
  turn) → ``terrariumAPI.switchCreatureModel`` /
  ``moduleAPI.toggle`` (plugin) / ``terrariumAPI.patchScratchpad`` →
  ``terrariumAPI.interruptCreature`` → ``terrariumAPI.getHistory`` →
  ``agentAPI.regenerate`` / ``agentAPI.editMessage`` +
  ``creature_branches`` → ``sessionAPI.stopActive``.
* :meth:`test_persistence_and_resume_journey` — run a turn so the
  ``.kohakutr`` fills → ``sessionAPI.list`` → ``getHistoryIndex`` /
  ``getHistory`` → ``sessionAPI.getTurns`` / ``getEvents`` →
  ``sessionAPI.stopActive`` → ``sessionAPI.resume`` →
  ``sessionAPI.delete``.
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
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

pytestmark = pytest.mark.timeout(60)

# Deterministic assistant replies. ScriptedLLM consumes the next
# unused entry per LLM call, respecting ``match`` against the last
# user message. A plain creature turn = one LLM call; a turn that
# emits a tool call = two (the call, then the post-tool continuation).
_REPLY_GREET = "Hello, I am the scripted e2e creature."
_REPLY_FOLLOWUP = "Here is my second scripted answer."
_TOOL_CALL = (
    "[/scratchpad]@@action=set\n@@key=topic\n@@value=e2e-journey\n[scratchpad/]"
)
_REPLY_AFTER_TOOL = "I stored the topic in the scratchpad."
# A slow reply — the provider yields it one char at a time with a
# per-chunk delay, so the turn stays in flight long enough for the
# journey to fire an interrupt over HTTP before it completes.
_REPLY_SLOW = "x" * 200

_CREATURE_CONFIG = """\
name: alice
system_prompt: "You are a deterministic e2e-test creature."
input:
  type: none
output:
  type: stdout
tools:
  - name: scratchpad
    type: builtin
"""


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Replace the live LLM provider at BOTH bind points.

    ``bootstrap.llm.create_llm_provider`` is the canonical factory;
    ``bootstrap.agent_init`` imports it by name, so without the second
    patch the agent-init path would still reach a real provider.
    """
    llm = ScriptedLLM(
        [
            ScriptEntry(_REPLY_GREET, match="hello creature"),
            ScriptEntry(_REPLY_FOLLOWUP, match="second question"),
            ScriptEntry(_TOOL_CALL, match="use the scratchpad"),
            ScriptEntry(_REPLY_AFTER_TOOL, match="topic"),
            ScriptEntry(
                _REPLY_SLOW, match="long turn", chunk_size=1, delay_per_chunk=0.05
            ),
            # Persistence journey + regenerate/edit fall through to the
            # generic tail entry below.
            ScriptEntry(_REPLY_GREET),
        ]
    )

    def _fake_create(config, llm_override=None):
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return llm


@pytest.fixture
def creature_dir(tmp_path: Path) -> Path:
    """Write a minimal but real on-disk creature config directory."""
    cdir = tmp_path / "alice"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(_CREATURE_CONFIG, encoding="utf-8")
    return cdir


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scripted_llm: ScriptedLLM,
) -> Iterator[TestClient]:
    """A TestClient over a real ``create_app()`` with a real service.

    ``KT_SESSION_DIR`` is redirected at ``tmp_path`` so persistence
    (saved-session list / resume / on-disk history) reads and writes
    the same isolated directory the engine saves into. ``set_service``
    snapshot/restore is handled by ``isolate_global_state`` (autouse),
    but we still explicitly reset to ``None`` after each journey.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    # The model-switch journey flips the creature to ``openai/gpt-4o-mini``;
    # the provider builder checks ``OPENAI_API_KEY`` before returning,
    # so without a value the switch raises ValueError -> 400.  The
    # ScriptedLLM intercepts the actual ``chat()`` call so this key is
    # never used over the wire — it just unblocks the provider build.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)

    app = create_app()
    # ``with TestClient`` runs the lifespan — startup attaches the
    # runtime-graph prompt, shutdown drives ``engine.shutdown()``.
    with TestClient(app) as test_client:
        yield test_client

    set_service(None)


# ── WS helpers ────────────────────────────────────────────────────────


def _stream_turn(ws, message: str) -> tuple[str, list[dict]]:
    """Send one user input over an attached IO WebSocket and collect
    the streamed assistant text + every activity frame, stopping at the
    post-turn ``idle`` frame."""
    ws.send_json({"type": "input", "content": message})
    chunks: list[str] = []
    activities: list[dict] = []
    while True:
        frame = ws.receive_json()
        ftype = frame.get("type")
        if ftype == "text":
            chunks.append(frame["content"])
        elif ftype == "activity":
            activities.append(frame)
        elif ftype == "idle":
            break
        elif ftype == "error":
            raise AssertionError(f"WS chat error frame: {frame!r}")
    return "".join(chunks), activities


# ── journeys ──────────────────────────────────────────────────────────


class TestApiCreatureJourney:
    """Fat end-to-end journeys over the real HTTP + WS API surface."""

    def test_chat_and_settings_journey(
        self, client: TestClient, creature_dir: Path, scripted_llm: ScriptedLLM
    ) -> None:
        """One whole UI session: create → list → multi-turn WS chat →
        tool-call turn → settings round-trip → interrupt → history →
        branch (regenerate + edit) → delete."""
        # 1. POST create a creature session (frontend: agentAPI.create).
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["status"] == "running"
        session_id = created["session_id"]
        creature_id = created["agent_id"]
        assert session_id and creature_id

        # 2. GET it appears in the canonical active-session list, and
        #    the unified getter resolves it by creature_id too.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert [s["session_id"] for s in resp.json()] == [session_id]

        resp = client.get(f"/api/sessions/active/{creature_id}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

        # 3. Open the IO WebSocket and stream a MULTI-turn conversation.
        #    Each scripted reply must stream back frame by frame.
        ws_url = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
        with client.websocket_connect(ws_url) as ws:
            info = ws.receive_json()
            assert info["activity_type"] == "session_info"
            assert info["agent_name"] == "alice"

            reply_one, _ = _stream_turn(ws, "hello creature")
            assert reply_one == _REPLY_GREET

            reply_two, _ = _stream_turn(ws, "second question please")
            assert reply_two == _REPLY_FOLLOWUP

            # 4. A turn that emits a tool call — the scratchpad write
            #    must execute and its result must surface in the stream
            #    as a ``tool_done`` activity, and the post-tool reply
            #    must stream back.
            reply_tool, activities = _stream_turn(ws, "use the scratchpad now")
            assert reply_tool == _REPLY_AFTER_TOOL
            tool_done = [a for a in activities if a.get("activity_type") == "tool_done"]
            assert tool_done, f"no tool_done activity in {activities!r}"
            done = tool_done[0]
            # The activity ``name`` is the job label (tool name + a
            # short job-id suffix), e.g. ``scratchpad[d72b8c]``.
            assert done["name"].startswith("scratchpad")
            # The scratchpad tool reported the key it set.
            assert "topic" in str(done.get("result") or done.get("output") or "")

        assert scripted_llm.call_count == 4  # 3 turns + 1 post-tool call

        # The tool's side effect is observable on the scratchpad GET.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/scratchpad"
        )
        assert resp.status_code == 200
        assert resp.json().get("topic") == "e2e-journey"

        # 5. Per-creature settings round-trip — change, then read back.
        # 5a. Model switch (frontend: terrariumAPI.switchCreatureModel).
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/model",
            json={"model": "openai/gpt-4o-mini"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "switched", "model": "openai/gpt-4o-mini"}
        resp = client.get(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["creatures"][0]["llm_name"] == "openai/gpt-4o-mini"

        # 5b. Plugin toggle (frontend: terrariumAPI.togglePlugin). The
        #     ``budget`` catalog plugin is registered disabled-but-
        #     available on every creature; flip it on and read back.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/plugins")
        assert resp.status_code == 200
        plugins = {p["name"]: p for p in resp.json()}
        assert "budget" in plugins
        assert plugins["budget"]["enabled"] is False

        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/plugins/budget/toggle",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "budget", "enabled": True}

        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/plugins")
        assert resp.status_code == 200
        assert {p["name"]: p["enabled"] for p in resp.json()}["budget"] is True

        # 5c. Scratchpad patch (frontend: terrariumAPI.patchScratchpad).
        resp = client.patch(
            f"/api/sessions/{session_id}/creatures/{creature_id}/scratchpad",
            json={"updates": {"focus": "e2e testing"}},
        )
        assert resp.status_code == 200
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/scratchpad"
        )
        assert resp.status_code == 200
        sp = resp.json()
        assert sp.get("focus") == "e2e testing"
        # The earlier tool-set key is still present — patch is additive.
        assert sp.get("topic") == "e2e-journey"

        # 5d. The per-creature state surface (frontend: the inspector
        #     panels). These all read straight off the live ``Agent``:
        #     system prompt, working dir, triggers, env, native tools.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/system-prompt"
        )
        assert resp.status_code == 200
        # ``alice``'s config declares this exact system prompt line.
        assert "deterministic e2e-test creature" in resp.json()["text"]

        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/working-dir"
        )
        assert resp.status_code == 200
        original_pwd = resp.json()["pwd"]
        assert original_pwd  # the engine resolved a concrete cwd

        # PUT a fresh working dir — ``creature_dir`` is a real directory.
        new_pwd = str(creature_dir)
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/{creature_id}/working-dir",
            json={"path": new_pwd},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "saved"
        assert body["pwd"] == str(Path(new_pwd).resolve())
        # The GET now reports the switched dir.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/working-dir"
        )
        assert resp.json()["pwd"] == str(Path(new_pwd).resolve())
        # A non-existent path is a clean 400 — workspace.set rejects it.
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/{creature_id}/working-dir",
            json={"path": str(creature_dir / "nope")},
        )
        assert resp.status_code == 400
        # ``/env`` resolves the same cwd as ``/working-dir``.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/env")
        assert resp.status_code == 200
        assert resp.json()["pwd"] == str(Path(new_pwd).resolve())

        # No triggers were installed on this creature → empty list.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/triggers"
        )
        assert resp.status_code == 200
        assert resp.json() == []

        # Native-tool inventory — ``alice`` runs in text tool-call mode
        # with only the ``scratchpad`` builtin, so no provider-native
        # tools are listed.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}" "/native-tool-options"
        )
        assert resp.status_code == 200
        assert resp.json() == {"tools": []}

        # 5e. Slash command surface (frontend: the macro shell / command
        #     palette). The ``status`` builtin user command reads live
        #     off the ``Agent`` — its output must carry the creature's
        #     real name and the live model identifier set in 5a.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/command",
            json={"command": "status", "args": ""},
        )
        assert resp.status_code == 200
        cmd_body = resp.json()
        assert cmd_body["command"] == "status"
        assert cmd_body["success"] is True
        # The status command reads live off the Agent — the creature's
        # real config name appears in its output.
        assert "alice" in cmd_body["output"]
        # It also reports the live conversation message count — three
        # streamed turns + one post-tool round produced a non-empty
        # conversation, so "Messages" is well above zero.
        assert "Messages" in cmd_body["output"]
        # An unknown command is a clean 400.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/command",
            json={"command": "definitely-not-a-command", "args": ""},
        )
        assert resp.status_code == 400

        # 5f. Jobs surface (frontend: the running-jobs panel). The
        #     creature is idle between turns, so its job list is empty,
        #     and stopping a non-existent job id is a clean 404.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/jobs")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/tasks/ghost_job_404/stop"
        )
        assert resp.status_code == 404

        # 6. Interrupt a turn via the API — open a fresh WS, fire a slow
        #    turn (~10s of char-by-char streaming), then POST interrupt
        #    as soon as the turn is processing. The turn must end
        #    cleanly: an ``interrupt`` activity then an ``idle``, and
        #    the slow reply must NOT have fully landed.
        with client.websocket_connect(ws_url) as ws:
            ws.receive_json()  # session_info
            ws.send_json({"type": "input", "content": "long turn now"})
            # Wait for the turn to actually be in flight before the
            # interrupt — ``processing_start`` is the engine's signal
            # that the controller loop has begun.
            started = False
            for _ in range(6):
                frame = ws.receive_json()
                if frame.get("type") == "processing_start":
                    started = True
                    break
                if frame.get("type") in {"idle", "error"}:
                    break
            assert started, "slow turn never reached processing_start"

            resp = client.post(
                f"/api/sessions/{session_id}/creatures/{creature_id}/interrupt"
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "interrupted"}

            saw_interrupt = False
            collected = ""
            while True:
                frame = ws.receive_json()
                ftype = frame.get("type")
                if ftype == "text":
                    collected += frame.get("content", "")
                elif ftype == "activity" and (
                    frame.get("activity_type") == "interrupt"
                ):
                    saw_interrupt = True
                elif ftype == "idle":
                    break
            assert saw_interrupt, "interrupt activity never surfaced on the WS"
            # Clean cancellation: the 200-char slow reply was cut short.
            assert collected != _REPLY_SLOW

        # 7. GET history — the turns we streamed are recorded; the
        #    interrupted slow reply did NOT fully land.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/history")
        assert resp.status_code == 200
        history = resp.json()
        messages = history["messages"]
        roles = [m.get("role") for m in messages]
        assert roles.count("user") >= 3
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        assert "hello creature" in joined
        assert _REPLY_GREET in joined
        assert _REPLY_FOLLOWUP in joined
        assert _REPLY_SLOW not in joined  # interrupted before completion

        # 8. Branch bookkeeping — regenerate the tail then edit a user
        #    message. Both open new branches at their turn; the history
        #    event log records multiple branch_ids for the same turn.
        events_before = history["events"]
        turn1_branches_before = {
            e.get("branch_id")
            for e in events_before
            if e.get("turn_index") == 1 and e.get("branch_id") is not None
        }
        assert turn1_branches_before == {1}

        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/regenerate",
            json={"turn_index": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "regenerating"

        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/history")
        assert resp.status_code == 200
        events_after_regen = resp.json()["events"]
        turn1_branches = {
            e.get("branch_id")
            for e in events_after_regen
            if e.get("turn_index") == 1 and e.get("branch_id") is not None
        }
        # Regenerate opened a second branch on turn 1.
        assert turn1_branches == {1, 2}

        # Edit the first user message and re-run — opens yet another
        # branch on turn 1 and the edited text becomes the live user
        # message (frontend: agentAPI.editMessage with turn_index).
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}" "/messages/1/edit",
            json={"content": "edited hello creature", "turn_index": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "edited"

        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/history")
        assert resp.status_code == 200
        edited_history = resp.json()
        turn1_branches = {
            e.get("branch_id")
            for e in edited_history["events"]
            if e.get("turn_index") == 1 and e.get("branch_id") is not None
        }
        assert turn1_branches == {1, 2, 3}
        live_user = [m for m in edited_history["messages"] if m.get("role") == "user"]
        assert any(
            "edited hello creature" in (m.get("content") or "") for m in live_user
        )

        # 9. DELETE the session — it leaves the active list.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert client.get("/api/sessions/active").json() == []

    def test_persistence_and_resume_journey(
        self, client: TestClient, creature_dir: Path, scripted_llm: ScriptedLLM
    ) -> None:
        """One whole UI session: create + run a turn → saved-session
        list → on-disk history index + per-target read → viewer turns
        + events → stop → resume → delete."""
        # Create + run a turn over the HTTP chat fallback so the
        # ``.kohakutr`` store fills (frontend: agentAPI.create then a
        # chat POST when the WS path is unavailable).
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        session_id = created["session_id"]
        creature_id = created["agent_id"]

        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/chat",
            json={"message": "persist this turn"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_GREET}

        # Saved-session list (frontend: sessionAPI.list with refresh).
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        listing = resp.json()
        assert listing["total"] == 1
        saved = listing["sessions"][0]
        saved_name = saved["name"]
        assert "alice" in saved["agents"]

        # On-disk history index + per-target history read.
        resp = client.get(f"/api/sessions/{saved_name}/history")
        assert resp.status_code == 200
        assert "alice" in resp.json().get("targets", [])

        resp = client.get(f"/api/sessions/{saved_name}/history/alice")
        assert resp.status_code == 200
        disk_blob = str(resp.json())
        assert "persist this turn" in disk_blob
        assert _REPLY_GREET in disk_blob

        # Viewer turns endpoint (frontend: sessionAPI.getTurns) — the
        # one turn we ran shows up as a rollup row for turn 1.
        resp = client.get(
            f"/api/sessions/{saved_name}/turns", params={"agent": "alice"}
        )
        assert resp.status_code == 200
        turns = resp.json()
        assert turns["total"] == 1
        assert turns["turns"][0]["turn_index"] == 1

        # Viewer events endpoint (frontend: sessionAPI.getEvents) — the
        # recorded event log carries the user_input for turn 1.
        resp = client.get(
            f"/api/sessions/{saved_name}/events", params={"agent": "alice"}
        )
        assert resp.status_code == 200
        ev_payload = resp.json()
        ev_types = {e.get("type") for e in ev_payload["events"]}
        assert "user_input" in ev_types
        assert all(e.get("turn_index") == 1 for e in ev_payload["events"])

        # Stop the live session first — on Windows the SQLite handle
        # must be released before resume re-opens the same file.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert client.get("/api/sessions/active").json() == []

        # Resume from disk (frontend: sessionAPI.resume).
        resp = client.post(f"/api/sessions/{saved_name}/resume")
        assert resp.status_code == 200
        resumed = resp.json()
        assert resumed["type"] == "agent"
        assert resumed["session_name"] == "alice"
        resumed_id = resumed["instance_id"]

        # The resumed session is live again in the active list, and its
        # restored conversation still carries the original turn.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert [s["session_id"] for s in resp.json()] == [resumed_id]

        resp = client.get(f"/api/sessions/{resumed_id}/creatures/alice/history")
        assert resp.status_code == 200
        resumed_history = resp.json()
        resumed_blob = str(resumed_history["messages"])
        assert "persist this turn" in resumed_blob
        assert _REPLY_GREET in resumed_blob
        # The restored turn/branch counters carried over: the recorded
        # run had exactly one turn on branch 1.
        turn1_branches = {
            e.get("branch_id")
            for e in resumed_history["events"]
            if e.get("turn_index") == 1 and e.get("branch_id") is not None
        }
        assert turn1_branches == {1}

        # The resumed creature accepts a fresh chat turn — proving the
        # rebuilt agent is fully live, not a frozen snapshot.
        resp = client.post(
            f"/api/sessions/{resumed_id}/creatures/alice/chat",
            json={"message": "post-resume turn"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_GREET}

        # Regenerate the resumed creature's first turn — opens a second
        # branch on turn 1, recorded in the event log.
        resp = client.post(
            f"/api/sessions/{resumed_id}/creatures/alice/regenerate",
            json={"turn_index": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "regenerating"
        resp = client.get(f"/api/sessions/{resumed_id}/creatures/alice/history")
        assert resp.status_code == 200
        turn1_branches_after = {
            e.get("branch_id")
            for e in resp.json()["events"]
            if e.get("turn_index") == 1 and e.get("branch_id") is not None
        }
        assert turn1_branches_after == {1, 2}

        # Stop it, then DELETE the saved file off disk.
        resp = client.delete(f"/api/sessions/active/{resumed_id}")
        assert resp.status_code == 200

        resp = client.delete(f"/api/sessions/{saved_name}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
