"""Wave D — v1 → v2 migration roundtrip.

Build a realistic v1 session, migrate it to v2, and confirm the v2
file reproduces the same conversation shape via
``replay_conversation``. The original v1 file must stay untouched.
"""

import hashlib

import pytest

from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.migrations import migrate
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import detect_format_version
from tests.unit.fixtures.sessions import build_v1_basic_session


@pytest.fixture
def v1_session(tmp_path):
    """Write a v1 .kohakutr and capture its bytes hash for tamper-check."""
    path = tmp_path / "alice.kohakutr"
    build_v1_basic_session(path, agent="alice")
    return path


def _file_hash(path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_migrates_to_v2_and_preserves_v1(v1_session, tmp_path):
    pre_hash = _file_hash(v1_session)
    assert detect_format_version(v1_session) == 1

    v2_path = migrate(v1_session, target_version=2)

    assert v2_path.name == "alice.kohakutr.v2"
    assert v2_path.exists()
    assert v1_session.exists(), "v1 file must never be deleted"
    assert _file_hash(v1_session) == pre_hash, "v1 file must never be modified"
    assert detect_format_version(v2_path) == 2


def test_replayed_conversation_matches_v1_snapshot(v1_session):
    v2_path = migrate(v1_session, target_version=2)

    # Load the v1 snapshot directly for the oracle.
    v1_store = SessionStore(v1_session)
    try:
        v1_snapshot = v1_store.load_conversation("alice")
    finally:
        v1_store.close(update_status=False)

    v2_store = SessionStore(v2_path)
    try:
        events = v2_store.get_events("alice")
        replayed = replay_conversation(events)
    finally:
        v2_store.close(update_status=False)

    # Replay yields state-bearing messages in the same order as the
    # v1 snapshot. The first message is the system prompt, then user,
    # then assistant with tool_calls, then tool result, then another
    # assistant message.
    roles = [m.get("role") for m in replayed]
    assert roles == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    v1_roles = [m.get("role") for m in (v1_snapshot or [])]
    assert roles == v1_roles

    # Content text should survive round-trip.
    assert replayed[1]["content"] == "read README and list files"
    assert replayed[2]["content"].startswith("Sure")
    assert replayed[2].get("tool_calls")
    assert replayed[3]["content"] == "# Project\nHello world\n"
    assert replayed[4]["content"] == "The README says hello world."


def test_migrated_meta_records_lineage(v1_session):
    v2_path = migrate(v1_session, target_version=2)
    store = SessionStore(v2_path)
    try:
        meta = store.load_meta()
    finally:
        store.close(update_status=False)

    assert meta["format_version"] == 2
    lineage = meta.get("migrated_from")
    assert isinstance(lineage, dict)
    assert lineage["source_version"] == 1
    assert "migrated_at" in lineage
    assert lineage["migrator"] == "v1_to_v2"
    # Nested lineage dict also carries migration record.
    outer = meta.get("lineage")
    assert isinstance(outer, dict)
    assert outer.get("migration", {}).get("migrator") == "v1_to_v2"


def test_v1_events_translated_to_v2_types(v1_session):
    """Wave-D rewrite: v1 events are TRANSLATED, not prefixed.

    The earlier design copied v1 events with a ``legacy:`` prefix,
    which made ``replay_conversation`` (and the frontend's replay)
    silently drop them. The new design rewrites each v1 event as the
    matching v2 type with proper ``turn_index`` / ``branch_id`` so the
    full pre-migration history is replayable. Every migrated event
    carries ``migrated: true`` for audit.
    """
    v2_path = migrate(v1_session, target_version=2)
    store = SessionStore(v2_path)
    try:
        events = store.get_events("alice")
    finally:
        store.close(update_status=False)

    # No event should have a ``legacy:`` prefix anymore.
    assert not any(e.get("type", "").startswith("legacy:") for e in events)

    # All migrated events carry the ``migrated`` flag.
    assert events, "migration must produce events"
    assert all(e.get("migrated") is True for e in events)

    # State-bearing v2 types must be present so backend replay works.
    types = {e.get("type") for e in events}
    assert "user_message" in types
    assert "text_chunk" in types
    assert "assistant_tool_calls" in types
    assert "tool_result" in types
    assert "system_prompt_set" in types


def test_scratchpad_and_state_copied(v1_session):
    v2_path = migrate(v1_session, target_version=2)
    store = SessionStore(v2_path)
    try:
        pad = store.load_scratchpad("alice")
        turn_count = store.load_turn_count("alice")
        token_usage = store.load_token_usage("alice")
    finally:
        store.close(update_status=False)

    assert pad == {"plan": "inspect repo", "status": "done"}
    assert turn_count == 1
    assert token_usage.get("total_input_tokens") == 500
