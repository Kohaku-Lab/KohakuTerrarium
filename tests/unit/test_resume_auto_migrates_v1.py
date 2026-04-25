"""Wave D — resume auto-migrates when only v1 exists.

Only ``alice.kohakutr`` (v1) is on disk. ``ensure_latest_version``
must create ``alice.kohakutr.v2`` beside it, preserve the v1, and
return the newly created path.
"""

import pytest

from kohakuterrarium.session.migrations import ensure_latest_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import detect_format_version
from tests.unit.fixtures.sessions import build_v1_basic_session


@pytest.fixture
def v1_only(tmp_path):
    path = tmp_path / "alice.kohakutr"
    build_v1_basic_session(path, agent="alice")
    return path


def test_auto_migration_creates_v2_and_preserves_v1(v1_only):
    v2_expected = v1_only.with_name("alice.kohakutr.v2")
    assert not v2_expected.exists()

    resolved = ensure_latest_version(v1_only)

    assert resolved == v2_expected
    assert v2_expected.exists()
    assert v1_only.exists(), "original v1 file must stay on disk"
    assert detect_format_version(resolved) == 2
    # v1 still reports as v1.
    assert detect_format_version(v1_only) == 1


def test_resume_open_helper_routes_to_v2(v1_only):
    from kohakuterrarium.session.resume import _open_store_with_migration

    store = _open_store_with_migration(v1_only)
    try:
        meta = store.load_meta()
        assert meta["format_version"] == 2
        assert meta.get("migrated_from", {}).get("source_version") == 1
    finally:
        store.close(update_status=False)


def test_second_call_is_idempotent(v1_only):
    first = ensure_latest_version(v1_only)
    first_mtime = first.stat().st_mtime

    # Second call should see the v2 as already present.
    import time

    time.sleep(0.05)
    second = ensure_latest_version(v1_only)
    assert second == first
    assert second.stat().st_mtime == first_mtime


def test_conversation_replays_after_auto_migration(v1_only):
    from kohakuterrarium.session.history import replay_conversation

    resolved = ensure_latest_version(v1_only)
    store = SessionStore(resolved)
    try:
        events = store.get_events("alice")
        replayed = replay_conversation(events)
    finally:
        store.close(update_status=False)

    roles = [m.get("role") for m in replayed]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
