"""Bounded historical image repair and durable branch checkpoints."""

import threading
from copy import deepcopy
from pathlib import Path

import pytest

from kohakuterrarium.errors import ConflictError, MediaReferenceError
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.conversation.continuation import persist_branch_view
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.conversation import media
from kohakuterrarium.core.conversation.media import (
    missing_history_images,
    repair_history_images,
)
from kohakuterrarium.llm.media.reference import (
    file_reference_path,
    resolve_session_image_urls,
)
from kohakuterrarium.session.resume.resume import inject_saved_state
from kohakuterrarium.session.resume.build import build_conversation
from kohakuterrarium.session.store.records.snapshot import save_agent_snapshot
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM


def image_message(url, turn=1, branch=1):
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": "Keep this text"},
            {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
        ],
        "metadata": {"turn_index": turn, "branch_id": branch},
    }


@pytest.fixture
async def history_agent(tmp_path):
    agent = await Agent.build(
        AgentConfig(name="media", system_prompt="plain"),
        llm=ScriptedLLM(["OK"]),
        io="none",
    )
    store = SessionStore(tmp_path / "repair.kohakutr")
    agent.attach_session_store(store)
    url = (tmp_path / "missing.png").as_uri()
    messages = [image_message(url), {"role": "assistant", "content": "Seen"}]
    for turn in (1, 2):
        store.append_event(
            "media",
            "user_message",
            {"content": messages[0]["content"] if turn == 1 else "continue"},
            turn_index=turn,
            branch_id=1,
            parent_branch_path=[] if turn == 1 else [(1, 1)],
        )
        store.append_event("media", "processing_end", {}, turn_index=turn, branch_id=1)
    messages.append(image_message(url, turn=2))
    agent.controller.conversation = build_conversation(messages)
    agent._turn_index = 2
    agent._branch_id = 1
    agent._parent_branch_path = [(1, 1)]
    save_agent_snapshot(store, "media", agent, messages)
    yield agent
    await agent.stop()
    store.close()


def test_only_absent_identified_completed_old_files(tmp_path, monkeypatch):
    missing = (tmp_path / "gone.png").as_uri()
    present = tmp_path / "present.png"
    present.write_bytes(b"still here")
    denied = tmp_path / "denied.png"
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        if path == denied:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    messages = [
        image_message(url)
        for url in (
            missing,
            present.as_uri(),
            denied.as_uri(),
            "/api/sessions/repair/artifacts/missing.png",
            "https://example.invalid/image.png",
            "file://remote/share/image.png",
            "data:image/png;base64,AAAA",
        )
    ]
    messages += [image_message(missing, turn=2), image_message(missing, turn=3)]
    messages.append(image_message(missing))
    messages[-1].pop("metadata")
    messages.append(image_message(missing, branch=2))
    events = [
        {"event_id": 1, "type": "processing_end", "turn_index": 1, "branch_id": 1}
    ]
    original = deepcopy(messages)
    found = missing_history_images(messages, events, {1: 1, 3: 1}, 3)
    assert [(item.message_index, item.part_index) for item in found] == [(0, 1)]
    assert messages == original
    for kind in ("processing_error", "activity:interrupt"):
        rejected = events + [
            {"event_id": 2, "type": kind, "turn_index": 1, "branch_id": 1}
        ]
        assert missing_history_images(messages, rejected, {1: 1, 3: 1}, 3) == []
    reopened = events + [
        {"event_id": 2, "type": "processing_start", "turn_index": 1, "branch_id": 1}
    ]
    assert missing_history_images(messages, reopened, {1: 1, 3: 1}, 3) == []


async def test_preview_confirm_resume_and_current_occurrence_protected(history_agent):
    agent = history_agent
    before = agent.controller.conversation.snapshot_messages()
    events = agent.session_store.get_events("media")
    found, token = await repair_history_images(agent)
    assert len(found) == 1
    assert agent.controller.conversation.snapshot_messages() == before
    await repair_history_images(agent, token)
    repaired = agent.controller.conversation.snapshot_messages()
    assert repaired[0]["content"][0] == before[0]["content"][0]
    assert "Historical image unavailable" in repaired[0]["content"][1]["text"]
    assert repaired[1:] == before[1:]
    with pytest.raises(
        MediaReferenceError, match="Required local media is unavailable"
    ):
        resolve_session_image_urls(
            agent.controller.conversation.to_messages(),
            session_path=agent.session_store.path,
        )
    assert agent.session_store.get_events("media") == events
    inject_saved_state(agent, agent.session_store, "media")
    assert agent.controller.conversation.snapshot_messages() == repaired
    assert (await repair_history_images(agent))[0] == []
    with pytest.raises(ConflictError, match="changed"):
        await repair_history_images(agent, token)


async def test_stale_preview_and_busy_history_leave_context_untouched(history_agent):
    agent = history_agent
    _, token = await repair_history_images(agent)
    agent.controller.conversation.append("user", "new content")
    before = agent.controller.conversation.snapshot_messages()
    with pytest.raises(ConflictError, match="changed"):
        await repair_history_images(agent, token)
    async with agent._processing_lock:
        with pytest.raises(ConflictError, match="active"):
            await repair_history_images(agent)
    assert agent.controller.conversation.snapshot_messages() == before


async def test_other_branch_checkpoint_and_original_events_preserved(history_agent):
    agent = history_agent
    store = agent.session_store
    sibling = [image_message("file:///sibling.png", branch=2)]
    store.append_event(
        "media",
        "user_message",
        {"content": sibling[0]["content"]},
        turn_index=1,
        branch_id=2,
    )
    store.save_branch_snapshot(
        "media",
        sibling,
        branch={
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        },
        watermark=store.max_event_id("media"),
    )
    before = store.load_branch_snapshot("media", {1: 2})
    _, token = await repair_history_images(agent)
    await repair_history_images(agent, token)
    assert store.load_branch_snapshot("media", {1: 2}) == before
    assert "Historical image unavailable" in str(
        store.load_branch_snapshot("media", {1: 1, 2: 1})
    )
    persist_branch_view(store, "media", {1: 2})
    inject_saved_state(agent, store, "media")
    assert "file:///sibling.png" in str(agent.controller.conversation.to_messages())
    assert "Historical image unavailable" not in str(
        agent.controller.conversation.to_messages()
    )
    persist_branch_view(store, "media", {1: 1, 2: 1})
    inject_saved_state(agent, store, "media")
    assert "Historical image unavailable" in str(
        agent.controller.conversation.to_messages()
    )


async def test_snapshot_failure_does_not_adopt_repair(history_agent, monkeypatch):
    agent = history_agent
    _, token = await repair_history_images(agent)
    before = agent.controller.conversation.snapshot_messages()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(agent.session_store, "save_branch_snapshot", fail)
    with pytest.raises(OSError, match="disk full"):
        await repair_history_images(agent, token)
    assert agent.controller.conversation.snapshot_messages() == before


@pytest.mark.parametrize("feedback", [False, True])
def test_tool_identity_must_match_the_containing_historical_turn(tmp_path, feedback):
    url = (tmp_path / "old.png").as_uri()
    tool = image_message(url)
    tool["role"] = "user" if feedback else "tool"
    tool["metadata"] = (
        {"kind": "tool_results", "job_ids": ["read_1"], "tool_result_phase": "final"}
        if feedback
        else {"job_id": "read_1", "tool_result_phase": "final"}
    )
    opener = image_message("https://example.invalid/retained.png")
    events = [
        {
            "event_id": 1,
            "type": "tool_call",
            "call_id": "read_1",
            "turn_index": 1,
            "branch_id": 1,
        },
        {"event_id": 2, "type": "processing_end", "turn_index": 1, "branch_id": 1},
    ]
    assert len(missing_history_images([opener, tool], events, {1: 1, 2: 1}, 2)) == 1
    assert missing_history_images([tool], events, {1: 1, 2: 1}, 2) == []
    current = image_message("https://example.invalid/current.png", turn=2)
    assert (
        missing_history_images([opener, current, tool], events, {1: 1, 2: 1}, 2) == []
    )
    for patch in ({"origin_known": False}, {"tool_result_phase": "pending"}):
        protected = deepcopy(tool)
        protected["metadata"].update(patch)
        assert (
            missing_history_images([opener, protected], events, {1: 1, 2: 1}, 2) == []
        )
    events.insert(
        1,
        {
            "event_id": 3,
            "type": "tool_call",
            "call_id": "read_1",
            "turn_index": 2,
            "branch_id": 1,
        },
    )
    assert missing_history_images([opener, tool], events, {1: 1, 2: 1}, 2) == []


async def test_restored_file_invalidates_confirmation(history_agent):
    agent = history_agent
    found, token = await repair_history_images(agent)
    url = found[0].url
    file_reference_path(url).write_bytes(b"restored")
    before = agent.controller.conversation.snapshot_messages()
    with pytest.raises(ConflictError, match="changed"):
        await repair_history_images(agent, token)
    assert agent.controller.conversation.snapshot_messages() == before


async def test_repair_reads_event_fields_off_the_event_loop(history_agent, monkeypatch):
    agent = history_agent
    store = agent.session_store
    store.append_event(
        "media",
        "tool_result",
        {"output": "x" * 4096, "job_id": "read_9"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 1)],
    )
    whole = store.get_events("media")
    loop_thread = threading.get_ident()
    reads = []
    read_fields = store.get_event_fields

    def spy(agent_name, fields):
        rows = read_fields(agent_name, fields)
        reads.append((threading.get_ident(), rows))
        return rows

    def whole_events(*args, **kwargs):
        raise AssertionError("repair must not load whole events")

    monkeypatch.setattr(store, "get_event_fields", spy)
    monkeypatch.setattr(store, "get_events", whole_events)
    found, token = await repair_history_images(agent)
    assert [item.turn_index for item in found] == [1]
    assert len(reads) == 1 and reads[0][0] != loop_thread
    assert all("output" not in row and "content" not in row for row in reads[0][1])
    kept = {"event_id", "type", "turn_index", "branch_id"}
    assert [{k: v for k, v in row.items() if k in kept} for row in reads[0][1]] == [
        {k: v for k, v in event.items() if k in kept} for event in whole
    ]
    paths = [
        row["parent_branch_path"]
        for row in reads[0][1]
        if row.get("parent_branch_path")
    ]
    assert len(paths) == 2 and all(path == ((1, 1),) for path in paths)
    assert all(path is paths[0] for path in paths)
    await repair_history_images(agent, token)
    assert "Historical image unavailable" in str(
        agent.controller.conversation.to_messages()
    )


async def test_history_changed_during_the_scan_is_a_conflict(
    history_agent, monkeypatch
):
    agent = history_agent
    _, token = await repair_history_images(agent)
    before = agent.controller.conversation.snapshot_messages()
    loop_thread = threading.get_ident()
    scan_threads = []

    def scan_then_advance(*args):
        scan_threads.append(threading.get_ident())
        found = missing_history_images(*args)
        agent._turn_index += 1
        return found

    monkeypatch.setattr(media, "missing_history_images", scan_then_advance)
    with pytest.raises(ConflictError, match="changed during the scan"):
        await repair_history_images(agent, token)
    assert scan_threads and scan_threads[0] != loop_thread
    assert agent.controller.conversation.snapshot_messages() == before
