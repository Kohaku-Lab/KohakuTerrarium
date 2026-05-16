"""B8 — multi-node creature tool calls render as orphan on host mirror.

User-reported symptom:
    "multi node creature have weirdly high amount of orphan tool call!
     this may means lot of cross node tooling stuff is problematic"

User clarification:
    "all the tool are success and agent DO receive content" — i.e. the
    tool actually executes and the agent's conversation receives the
    result.  The orphan flag is an **observability / pairing** artefact
    in the host's session-event mirror, not a functional delivery bug.

Mechanism (see ``temp/bugs/B8.md`` Stage 3):
    * The worker writes ``tool_call`` and ``tool_result`` events
      (`session/output.py:_handle_tool_start`/`_handle_tool_done`),
      both keyed by ``call_id == metadata["job_id"]``. Within the
      events table these pair correctly.
    * The runtime does NOT emit a corresponding ``assistant_tool_calls``
      event (the live conversation snapshot carries the provider-side
      ``tool_call_id`` advertisement, and snapshots are not mirrored
      because ``SessionEventTee`` only subscribes to ``append_event``).
    * The host mirror therefore has a ``tool_result`` with a
      ``call_id`` that no preceding ``assistant_tool_calls`` event
      announced. When ``session/history.py:replay_conversation``
      rebuilds the message list from that mirror, every ``role=tool``
      message lacks an announcing assistant; then
      ``Conversation.sanitize_orphan_tool_pairs`` drops it as orphan
      and logs ``"dropped orphan tool-result message"``.

The test pins both halves:

  1. The tool actually delivered (worker conversation has the result;
     WS stream has ``tool_start`` + ``tool_done`` typed-mirror frames
     with matching ``job_id``).
  2. The host mirror's replayed conversation triggers the orphan
     warning when run through the sanitiser — i.e. every tool call
     surfaces as orphan to Studio's mirror-based surfaces.

The framework logger sets ``propagate=False`` on the
``kohakuterrarium`` root; we attach a plain ``logging.Handler``
directly to ``kohakuterrarium.core.conversation`` for capture.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.history import (
    normalize_resumable_events,
    replay_conversation,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptEntry

from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabWorker,
    install_scripted_llm,
)

pytestmark = pytest.mark.timeout(180)


# Bracket-format scratchpad call — same shape as the standalone
# ``test_api_creature`` journey. The match-gated entries make the
# script order-independent.
_TOOL_CALL = "[/scratchpad]@@action=set\n@@key=topic\n@@value=e2e-b8\n[scratchpad/]"
_REPLY_AFTER_TOOL = "I stored the topic in the scratchpad (b8)."


class _CapturingHandler(logging.Handler):
    """Record every emitted log record for later inspection."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


def _write_creature_config(root: Path, name: str, system_prompt: str) -> Path:
    """Write an on-disk creature config that enables the scratchpad tool."""
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        f"system_prompt: {system_prompt!r}\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "tool_format: bracket\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n"
        "tools:\n"
        "  - name: scratchpad\n"
        "    type: builtin\n",
        encoding="utf-8",
    )
    return cdir


async def _drain_chat_turn(
    ws,
    message: str,
    *,
    idle: float = 4.0,
    hard: float = 25.0,
) -> tuple[str, list[dict]]:
    """Send one user input over an attached IO WS and return (text, frames).

    Stops on ``idle`` (post-turn marker) or on read-timeout. Captures
    every frame so the test can assert on the typed-mirror
    ``tool_start`` / ``tool_done`` frames.
    """
    await ws.send(json.dumps({"type": "input", "content": message}))
    chunks: list[str] = []
    frames: list[dict] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + hard
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle)
        except asyncio.TimeoutError:
            break
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            continue
        frames.append(frame)
        t = frame.get("type")
        if t in ("text", "text_chunk"):
            chunks.append(str(frame.get("content", "")))
        elif t == "error":
            chunks.append(f"<ERROR:{frame.get('content')}>")
            break
        elif t == "idle":
            break
    return "".join(chunks), frames


class TestMultinodeB8OrphanToolCalls:
    """Mirror-based history surfaces tool calls as orphan on multi-node."""

    async def test_tool_calls_replay_as_orphan_on_host_mirror(
        self, tmp_path, monkeypatch
    ):
        # KT_SESSION_DIR controls where the host writes its session
        # mirror — we read straight from that dir below to walk the
        # mirrored events without going through HTTP.
        host_session_dir = tmp_path / "host-sessions"
        monkeypatch.setenv("KT_SESSION_DIR", str(host_session_dir))

        install_scripted_llm(
            monkeypatch,
            script=[
                # Tool-call turn — must include the bracket-form scratchpad
                # call. The agent runs the tool, receives the result, and
                # the controller calls the LLM again for a continuation.
                ScriptEntry(_TOOL_CALL, match="run scratchpad once"),
                # Post-tool continuation reply. Matches the "scratchpad"
                # / "topic" stamp the tool result includes in its output.
                ScriptEntry(_REPLY_AFTER_TOOL, match="topic"),
                # Tail fallback so any unexpected extra LLM call doesn't
                # blow up the run with an "out of script" surprise.
                ScriptEntry("ok"),
            ],
        )

        cfg_alpha = _write_creature_config(tmp_path, "alpha", "You are alpha (b8).")

        # Attach our log handler BEFORE any creature is built so we
        # capture the WARNINGs that the orphan-sanitiser logs.
        conv_logger = logging.getLogger("kohakuterrarium.core.conversation")
        handler = _CapturingHandler()
        prior_level = conv_logger.level
        conv_logger.addHandler(handler)
        conv_logger.setLevel(logging.DEBUG)
        try:
            async with RealLabHost(tmp_path) as host:
                async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1:
                    await RealLabHost._wait_for(
                        lambda: w1.node_id in set(host.host_engine.alive_clients()),
                        "worker w1 joins",
                    )

                    # Spawn alpha on the remote worker.
                    r_spawn = await asyncio.wait_for(
                        host.http.post(
                            "/api/sessions/active/creature",
                            json={
                                "config_path": str(cfg_alpha),
                                "on_node": w1.node_id,
                            },
                        ),
                        timeout=OP_TIMEOUT * 4,
                    )
                    assert (
                        r_spawn.status_code == 200
                    ), f"spawn alpha on w1: {r_spawn.status_code} {r_spawn.text}"
                    spawn = r_spawn.json()
                    session_id = spawn["session_id"]
                    creature_id = spawn["creatures"][0]["creature_id"]

                    # Drive the tool-call turn over the chat WS.
                    chat_url = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
                    async with host.api_ws(chat_url) as ws:
                        text, frames = await _drain_chat_turn(ws, "run scratchpad once")

                    # --- Half 1a: tool actually executed and replied ----
                    # The post-tool LLM reply must have streamed back —
                    # this is the user's "agent DO receive content"
                    # observation. (The tool result content gets folded
                    # into the next LLM call's user message; the reply
                    # the user sees is _REPLY_AFTER_TOOL.)
                    assert _REPLY_AFTER_TOOL in text, (
                        "agent did not receive the tool result on its "
                        f"controller loop; text={text!r}"
                    )

                    # --- Half 1b: WS stream carried both lifecycle frames
                    # Typed-mirror frames have ``type`` == activity_type
                    # (see ``studio/attach/_event_stream.py:_emit_typed_mirror``).
                    tool_start_frames = [
                        f for f in frames if f.get("type") == "tool_start"
                    ]
                    tool_done_frames = [
                        f
                        for f in frames
                        if f.get("type") in ("tool_done", "tool_error")
                    ]
                    assert tool_start_frames, (
                        "no tool_start typed-mirror frame on the WS — "
                        f"frames: {[f.get('type') for f in frames]!r}"
                    )
                    assert tool_done_frames, (
                        "no tool_done/tool_error typed-mirror frame on the "
                        f"WS — frames: {[f.get('type') for f in frames]!r}"
                    )
                    # Both frames carry the same job_id (the runtime's
                    # correlation key for the session log).
                    start_job_ids = {
                        f.get("job_id") for f in tool_start_frames if f.get("job_id")
                    }
                    done_job_ids = {
                        f.get("job_id") for f in tool_done_frames if f.get("job_id")
                    }
                    assert start_job_ids == done_job_ids and start_job_ids, (
                        f"WS tool_start/tool_done job_ids must match — "
                        f"start={start_job_ids!r} done={done_job_ids!r}"
                    )

                    # Settle so the session-sync tee has flushed every
                    # event to the host's mirror store.
                    await asyncio.sleep(0.5)

                    # --- Half 2a: event-level pairing is fine ----------
                    # Walk the host's mirror events for this session and
                    # confirm tool_call <-> tool_result pair by call_id.
                    mirror_path = host_session_dir / "mirror" / f"{session_id}.kohakutr"
                    assert mirror_path.exists(), (
                        f"host mirror not written for session {session_id!r}; "
                        f"looked at {mirror_path!s}"
                    )
                    mirror_store = SessionStore(str(mirror_path))
                    try:
                        events = mirror_store.get_events("alpha")
                    finally:
                        mirror_store.close()

                    tool_call_evts = [e for e in events if e.get("type") == "tool_call"]
                    tool_result_evts = [
                        e for e in events if e.get("type") == "tool_result"
                    ]
                    assert tool_call_evts, (
                        "no tool_call event mirrored to host store — the "
                        f"session-sync tee did not deliver. events: "
                        f"{[e.get('type') for e in events]!r}"
                    )
                    assert tool_result_evts, (
                        "no tool_result event mirrored to host store — "
                        f"the session-sync tee did not deliver. events: "
                        f"{[e.get('type') for e in events]!r}"
                    )
                    call_ids_start = {e.get("call_id") for e in tool_call_evts}
                    call_ids_done = {e.get("call_id") for e in tool_result_evts}
                    assert call_ids_start == call_ids_done, (
                        "event-level call_id pairing broken on host mirror — "
                        f"starts={call_ids_start!r} dones={call_ids_done!r}"
                    )
                    # ``normalize_resumable_events`` should not synthesise
                    # any ghost interrupted tool_result.
                    normalized = normalize_resumable_events(events)
                    synthetic = [e for e in normalized if e.get("_synthetic_resume")]
                    assert not synthetic, (
                        "normalize_resumable_events synthesised a ghost "
                        f"tool_result for an unpaired call: {synthetic!r}"
                    )

                    # --- Half 2b: REPLAY through the mirror is broken --
                    # When Studio's mirror-based history surface
                    # (viewer / fork / persistence) rebuilds the
                    # conversation from these events, every tool_result
                    # lacks an announcing ``assistant_tool_calls`` event
                    # (the runtime never emits one — see B8.md Stage 3).
                    # Pushing the result through the orphan sanitiser
                    # then drops each tool message and logs a WARNING.
                    # B8: that WARNING fires; the user sees it as "high
                    # orphan rate".
                    messages = replay_conversation(normalized)
                    # Sanity: replay produced at least one role=tool
                    # message — otherwise this test isn't even hitting
                    # the orphan code path.
                    tool_msgs = [m for m in messages if m.get("role") == "tool"]
                    assert tool_msgs, (
                        "replay_conversation produced no role=tool "
                        f"messages from mirror events; messages={messages!r}"
                    )

                    handler.records.clear()
                    sanitised = Conversation.sanitize_orphan_tool_pairs(messages)
                    orphan_warns = [
                        r
                        for r in handler.records
                        if r.levelno >= logging.WARNING
                        and "orphan" in str(r.getMessage()).lower()
                    ]
                    # Defensive: confirm the handler is attached at the
                    # right logger before asserting on absence.
                    assert handler.records or sanitised == messages, (
                        "no log records captured from "
                        "kohakuterrarium.core.conversation — the test's "
                        "handler attachment is wrong, not the framework."
                    )
                    surviving_tool_msgs = [
                        m for m in sanitised if m.get("role") == "tool"
                    ]
                    # B8 assertion: the orphan sanitiser must NOT drop
                    # any tool result message replayed from the host
                    # mirror, AND it must NOT log an orphan warning.
                    # Today both fail: the assistant tool_calls
                    # advertisement is missing from the event stream,
                    # so every tool message is dropped as orphan.
                    assert not orphan_warns, (
                        "B8: replaying host-mirror events through the "
                        "orphan sanitiser dropped one or more tool-result "
                        "messages as orphan (the conversation rebuilder "
                        "had no announcing assistant_tool_calls event). "
                        f"Warnings captured: "
                        f"{[r.getMessage() for r in orphan_warns]!r}"
                    )
                    assert len(surviving_tool_msgs) == len(tool_msgs), (
                        "B8: orphan sanitiser dropped "
                        f"{len(tool_msgs) - len(surviving_tool_msgs)} of "
                        f"{len(tool_msgs)} tool message(s) replayed from "
                        "the host mirror — every tool call surfaces as "
                        "orphan on multi-node Studio surfaces."
                    )
        finally:
            conv_logger.removeHandler(handler)
            conv_logger.setLevel(prior_level)
