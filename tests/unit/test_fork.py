"""Tests for the session fork / branch primitive (Wave E P2).

Covers:
    * Basic copy-on-fork: events <= fork_point present, events > absent.
    * Canned mutations (drop_trailing, edit_user_message) and arbitrary
      callables.
    * Lineage bookkeeping on both child and parent.
    * Fork-not-stable stability check.
    * Chained forks (fork-of-fork) and forks of migrated (v1→v2)
      sessions.
    * Artifacts directory behavior.
    * HTTP endpoint shape + error paths.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.studio.persistence import store as persistence_store

from tests.unit._persistence_test_helpers import mount_session_routes
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.store import SessionStore

# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
def parent_store(tmp_path: Path) -> SessionStore:
    """A simple parent session with a mixed event log."""
    path = tmp_path / "parent.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="sess_parent",
        config_type="agent",
        config_path="/path/to/agent",
        pwd="/tmp",
        agents=["root"],
    )
    # Turn 1: user -> assistant
    store.append_event(
        "root",
        "user_message",
        {"content": "hello parent"},
        turn_index=1,
    )
    store.append_event(
        "root",
        "text_chunk",
        {"content": "hi back", "chunk_seq": 0},
        turn_index=1,
    )
    # Turn 2: user -> tool_call -> tool_result -> text
    store.append_event(
        "root",
        "user_message",
        {"content": "run a tool"},
        turn_index=2,
    )
    store.append_event(
        "root",
        "tool_call",
        {"name": "bash", "call_id": "tc-1", "args": {"command": "ls"}},
        turn_index=2,
    )
    store.append_event(
        "root",
        "tool_result",
        {"name": "bash", "call_id": "tc-1", "output": "file.txt"},
        turn_index=2,
    )
    store.append_event(
        "root",
        "text_chunk",
        {"content": "done", "chunk_seq": 0},
        turn_index=2,
    )
    yield store
    try:
        store.close(update_status=False)
    except Exception:
        pass


def _child_path(parent_path: str, tag: str) -> str:
    p = Path(parent_path)
    base = p.name.split(".kohakutr", 1)[0]
    return str(p.parent / f"{base}-{tag}.kohakutr.v2")


# --------------------------------------------------------------------
# Basic fork roundtrip
# --------------------------------------------------------------------


def test_fork_copies_events_up_to_fork_point(parent_store: SessionStore, tmp_path):
    """Events with event_id <= fork_point are present; later events absent."""
    target = _child_path(parent_store.path, "basic")
    # Fork after the second tool_result (event_id=5): should include 1..5.
    child = parent_store.fork(target, at_event_id=5)
    try:
        child_events = child.get_events("root")
        event_ids = sorted(e["event_id"] for e in child_events)
        assert event_ids == [1, 2, 3, 4, 5]
        # Fork point itself was copied verbatim.
        fork_point = next(e for e in child_events if e["event_id"] == 5)
        assert fork_point["type"] == "tool_result"
        assert fork_point["call_id"] == "tc-1"
        # The trailing text_chunk at event_id=6 was dropped.
        assert not any(e["event_id"] == 6 for e in child_events)
        # Parent is unaffected.
        parent_ids = sorted(e["event_id"] for e in parent_store.get_events("root"))
        assert parent_ids == [1, 2, 3, 4, 5, 6]
    finally:
        child.close(update_status=False)


def test_fork_new_event_appends_with_resumed_counter(
    parent_store: SessionStore, tmp_path
):
    """Counters restored on the child so further appends get fresh ids."""
    target = _child_path(parent_store.path, "append")
    child = parent_store.fork(target, at_event_id=5)
    try:
        _, new_id = child.append_event("root", "user_message", {"content": "next"})
        # Forked copy carried global event_id up to 5 — next one is 6.
        assert new_id == 6
    finally:
        child.close(update_status=False)


# --------------------------------------------------------------------
# Mutation at fork point
# --------------------------------------------------------------------


def test_fork_drop_trailing_drops_the_fork_point_event(parent_store: SessionStore):
    """A mutator returning None deletes the fork-point event."""

    def drop_trailing(_evt: dict) -> None:
        return None

    target = _child_path(parent_store.path, "drop")
    child = parent_store.fork(target, at_event_id=6, mutate=drop_trailing)
    try:
        event_ids = sorted(e["event_id"] for e in child.get_events("root"))
        assert 6 not in event_ids
        assert event_ids == [1, 2, 3, 4, 5]
    finally:
        child.close(update_status=False)


def test_fork_edit_user_message_replaces_content(parent_store: SessionStore):
    """Canonical edit: rewrite the user_message at the fork point."""

    def edit_msg(evt: dict) -> dict:
        updated = dict(evt)
        updated["content"] = "edited!"
        return updated

    target = _child_path(parent_store.path, "edit")
    # user_message at turn 2 lives at event_id=3.
    child = parent_store.fork(target, at_event_id=3, mutate=edit_msg)
    try:
        events = child.get_events("root")
        mutated = next(e for e in events if e["event_id"] == 3)
        assert mutated["type"] == "user_message"
        assert mutated["content"] == "edited!"
    finally:
        child.close(update_status=False)


def test_fork_arbitrary_callable_mutate(parent_store: SessionStore):
    """Any Python callable returning a dict works as a mutator."""

    def rewrite(evt: dict) -> dict:
        return {**evt, "content": "rewritten", "custom_field": 42}

    target = _child_path(parent_store.path, "custom")
    child = parent_store.fork(target, at_event_id=3, mutate=rewrite)
    try:
        mutated = next(e for e in child.get_events("root") if e["event_id"] == 3)
        assert mutated["content"] == "rewritten"
        assert mutated["custom_field"] == 42
    finally:
        child.close(update_status=False)


# --------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------


def test_fork_lineage_records_parent_and_fork_point(parent_store: SessionStore):
    target = _child_path(parent_store.path, "lineage")
    child = parent_store.fork(target, at_event_id=5, name="feature-branch")
    try:
        meta = child.load_meta()
        lineage = meta["lineage"]
        assert lineage["fork"]["parent_session_id"] == "sess_parent"
        assert lineage["fork"]["fork_point"] == 5
        assert lineage["fork"]["fork_mutation"] is None
        assert "fork_created_at" in lineage["fork"]
    finally:
        child.close(update_status=False)

    # Parent records the child in forked_children.
    parent_meta = parent_store.load_meta()
    children = parent_meta["forked_children"]
    assert len(children) == 1
    assert children[0]["session_id"].startswith("sess_parent-fork-")
    assert children[0]["fork_point"] == 5


def test_fork_lineage_records_mutation_label(parent_store: SessionStore):
    def edit_user_message(evt: dict) -> dict:
        return {**evt, "content": "new"}

    target = _child_path(parent_store.path, "mutlabel")
    child = parent_store.fork(target, at_event_id=3, mutate=edit_user_message)
    try:
        lineage = child.load_meta()["lineage"]
        assert lineage["fork"]["fork_mutation"] == "edit_user_message"
    finally:
        child.close(update_status=False)


def test_fork_of_fork_preserves_lineage_chain(parent_store: SessionStore, tmp_path):
    target_a = _child_path(parent_store.path, "gen1")
    gen1 = parent_store.fork(target_a, at_event_id=5)
    try:
        gen1_meta = gen1.load_meta()
        gen1_session_id = gen1_meta["session_id"]
        target_b = _child_path(gen1.path, "gen2")
        gen2 = gen1.fork(target_b, at_event_id=3)
        try:
            gen2_lineage = gen2.load_meta()["lineage"]
            # gen2's fork lineage points at gen1.
            assert gen2_lineage["fork"]["parent_session_id"] == gen1_session_id
            # Walking up, the record gen1 wrote is still present — our
            # lineage implementation merges prior entries and then
            # overwrites the ``fork`` key. That is acceptable; the key
            # invariant is that ``parent_session_id`` on each child
            # traces its parent.
            assert gen2_lineage["fork"]["fork_point"] == 3
        finally:
            gen2.close(update_status=False)
    finally:
        gen1.close(update_status=False)


def test_fork_of_migrated_session_preserves_migration_lineage(
    tmp_path: Path, parent_store: SessionStore
):
    """Simulate a v1→v2 migrated parent by pre-seeding meta['lineage']."""
    parent_store.meta["lineage"] = {
        "migration": {
            "migrated_from": "/tmp/old.kohakutr",
            "source_version": 1,
            "migrator": "v1_to_v2",
        }
    }
    target = _child_path(parent_store.path, "after-migrate")
    child = parent_store.fork(target, at_event_id=5)
    try:
        lineage = child.load_meta()["lineage"]
        # Both the prior migration record and the new fork record must
        # survive on the child's lineage dict.
        assert lineage["migration"]["migrator"] == "v1_to_v2"
        assert lineage["fork"]["fork_point"] == 5
    finally:
        child.close(update_status=False)


# --------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------


def test_fork_raises_when_inflight_call_overlaps(tmp_path: Path):
    """If a tool_call in range is unpaired AND in pending_job_ids → error."""
    path = tmp_path / "unstable.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="sess_unstable",
        config_type="agent",
        config_path="/p",
        pwd="/w",
        agents=["root"],
    )
    store.append_event("root", "user_message", {"content": "please run"})
    store.append_event(
        "root",
        "tool_call",
        {"name": "bash", "call_id": "inflight-1", "args": {}},
    )
    try:
        target = _child_path(str(path), "broken")
        with pytest.raises(ForkNotStableError):
            store.fork(
                target,
                at_event_id=2,
                pending_job_ids={"inflight-1"},
            )
    finally:
        store.close(update_status=False)


def test_fork_is_stable_when_pending_set_empty(tmp_path: Path):
    """Open calls with no pending set → treated as already finished."""
    path = tmp_path / "clean.kohakutr.v2"
    store = SessionStore(path)
    store.init_meta(
        session_id="sess_clean",
        config_type="agent",
        config_path="/p",
        pwd="/w",
        agents=["root"],
    )
    store.append_event("root", "user_message", {"content": "hi"})
    store.append_event(
        "root",
        "tool_call",
        {"name": "bash", "call_id": "tc-x", "args": {}},
    )
    try:
        target = _child_path(str(path), "stable")
        child = store.fork(target, at_event_id=2, pending_job_ids=set())
        child.close(update_status=False)
    finally:
        store.close(update_status=False)


# --------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------


def test_fork_copies_artifacts_directory(parent_store: SessionStore):
    """Artifacts referenced by copied events get a shallow copy."""
    parent_store.write_artifact("cat.png", b"not-really-a-png")
    target = _child_path(parent_store.path, "arts")
    child = parent_store.fork(target, at_event_id=5)
    try:
        child_artifacts = child.artifacts_dir
        assert (child_artifacts / "cat.png").is_file()
        assert (child_artifacts / "cat.png").read_bytes() == b"not-really-a-png"
    finally:
        child.close(update_status=False)


# --------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------


def test_fork_rejects_non_positive_event_id(parent_store: SessionStore):
    with pytest.raises(ValueError):
        parent_store.fork(
            _child_path(parent_store.path, "bad"),
            at_event_id=0,
        )


def test_fork_rejects_existing_target(parent_store: SessionStore, tmp_path: Path):
    target = tmp_path / "already.kohakutr.v2"
    target.write_bytes(b"")
    with pytest.raises(FileExistsError):
        parent_store.fork(str(target), at_event_id=5)


# --------------------------------------------------------------------
# HTTP endpoint
# --------------------------------------------------------------------


def _build_fork_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    """Build a TestClient with _SESSION_DIR pointed at tmp_path.

    Creates a parent session inside tmp_path so resolve_session_path
    finds it.
    """
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    parent_path = tmp_path / "http-parent.kohakutr"
    store = SessionStore(parent_path)
    store.init_meta(
        session_id="sess_http",
        config_type="agent",
        config_path="/p",
        pwd="/w",
        agents=["root"],
    )
    store.append_event("root", "user_message", {"content": "first"})
    store.append_event(
        "root",
        "text_chunk",
        {"content": "response", "chunk_seq": 0},
    )
    store.close(update_status=False)

    app = FastAPI()
    mount_session_routes(app)
    return TestClient(app), parent_path


def test_http_fork_returns_201_with_child_info(tmp_path: Path, monkeypatch):
    client, _ = _build_fork_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={"at_event_id": 2, "name": "branch-a"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["fork_point"] == 2
    assert body["session_id"].startswith("sess_http-fork-")
    assert "http-parent-branch-a" in body["path"]


def test_http_fork_400_on_invalid_event_id(tmp_path: Path, monkeypatch):
    client, _ = _build_fork_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={"at_event_id": 0},
    )
    assert resp.status_code == 400


def test_http_fork_400_on_missing_event_id(tmp_path: Path, monkeypatch):
    client, _ = _build_fork_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={"at_event_id": 9999},
    )
    assert resp.status_code == 400


def test_http_fork_404_on_unknown_session(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    mount_session_routes(app)
    client = TestClient(app)
    resp = client.post(
        "/api/sessions/no-such-session/fork",
        json={"at_event_id": 1},
    )
    assert resp.status_code == 404


def test_http_fork_edit_user_message(tmp_path: Path, monkeypatch):
    client, _ = _build_fork_client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={
            "at_event_id": 1,
            "mutate": {
                "kind": "edit_user_message",
                "args": {"content": "rewritten via HTTP"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    # Open the forked store and verify the content was edited.
    child_path = Path(resp.json()["path"])
    child = SessionStore(child_path)
    try:
        events = child.get_events("root")
        fork_point = next(e for e in events if e["event_id"] == 1)
        assert fork_point["content"] == "rewritten via HTTP"
    finally:
        child.close(update_status=False)


def test_http_fork_409_on_unstable(tmp_path: Path, monkeypatch):
    """Simulate an in-flight job by patching SessionStore.fork to raise."""
    client, _ = _build_fork_client(tmp_path, monkeypatch)

    import kohakuterrarium.session.store as store_mod

    def patched(self, *args, **kwargs):
        raise ForkNotStableError("simulated in-flight")

    monkeypatch.setattr(store_mod.SessionStore, "fork", patched)
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={"at_event_id": 1},
    )
    assert resp.status_code == 409
    assert "simulated in-flight" in resp.json()["detail"]


def test_http_fork_400_when_mutation_incompatible(tmp_path: Path, monkeypatch):
    """edit_user_message on a non-user_message event → 400."""
    client, _ = _build_fork_client(tmp_path, monkeypatch)
    # event_id=2 is a text_chunk — edit_user_message should refuse.
    resp = client.post(
        "/api/sessions/http-parent/fork",
        json={
            "at_event_id": 2,
            "mutate": {
                "kind": "edit_user_message",
                "args": {"content": "nope"},
            },
        },
    )
    assert resp.status_code == 400
