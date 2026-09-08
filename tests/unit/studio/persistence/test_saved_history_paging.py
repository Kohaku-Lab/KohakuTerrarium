"""Saved-session paged history helper tests.

``history_page_from_store`` is the read path shared by the saved-history HTTP
route and the engine-owned live-store path: given an open ``SessionStore`` and a
target it returns a bounded, cursor-driven page reusing the session pager. This
is the piece that was NOT wired before: the saved ``/history/{target}`` route
only ever returned the full snapshot.
"""

from __future__ import annotations

import pytest

from kohakuterrarium.session.history_paging import HistoryPagingError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.history import history_page_from_store


@pytest.fixture()
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    s.close()


def _events(store, content, agent="ag"):
    for text in content:
        store.append_event(agent, "text", {"content": text})


def _cursor(history_page, which):
    value = history_page[which]
    assert value is not None
    return value


def test_saved_event_page_envelope(store):
    _events(store, ["c0", "c1", "c2", "c3"])
    page = history_page_from_store(
        store, session_id="s", session_name="s", target="ag", limit=2
    )
    assert [item["content"] for item in page["events"]] == ["c2", "c3"]
    assert page["messages"] == []
    hp = page["history_page"]
    assert hp["version"] == 1
    assert hp["stream"] == "events"
    assert hp["has_older"] is True
    assert hp["has_newer"] is False
    assert hp["reset_required"] is False
    assert hp["history_id"]
    assert page["is_processing"] is False
    assert page["live_job_ids"] == []


def test_saved_event_traversal_no_holes_or_repeats(store):
    _events(store, ["c0", "c1", "c2", "c3", "c4"])
    seen: list[str] = []
    before = None
    while True:
        page = history_page_from_store(
            store,
            session_id="s",
            session_name="s",
            target="ag",
            limit=2,
            before=before,
        )
        if not page["events"]:
            break
        seen = [item["_history_key"] for item in page["events"]] + seen
        before = _cursor(page["history_page"], "before")
    assert len(seen) == 5
    assert len(set(seen)) == len(seen)


def test_saved_snapshot_page(store):
    store.save_conversation("ag", [{"role": "user", "content": "u0"}])
    page = history_page_from_store(
        store, session_id="s", session_name="s", target="ag", stream="snapshot", limit=1
    )
    assert [item["content"] for item in page["messages"]] == ["u0"]
    assert page["events"] == []
    assert page["history_page"]["stream"] == "snapshot"


def test_saved_channel_target_uses_channel_stream(store):
    store.save_channel_message("room", {"sender": "a", "content": "hello"})
    page = history_page_from_store(
        store, session_id="s", session_name="s", target="ch:room", limit=5
    )
    assert page["history_page"]["stream"] == "channel"
    assert [item["content"] for item in page["messages"]] == ["hello"]
    assert page["events"] == []


def test_saved_channel_stream_without_ch_target_rejected(store):
    with pytest.raises(HistoryPagingError):
        history_page_from_store(
            store,
            session_id="s",
            session_name="s",
            target="ag",
            stream="channel",
            limit=5,
        )


def test_saved_unsupported_stream_is_explicit(store):
    with pytest.raises(HistoryPagingError):
        history_page_from_store(
            store,
            session_id="s",
            session_name="s",
            target="ag",
            stream="bogus",
            limit=5,
        )


def test_saved_nonpositive_limit_rejected(store):
    with pytest.raises(HistoryPagingError):
        history_page_from_store(
            store,
            session_id="s",
            session_name="s",
            target="ag",
            stream="events",
            limit=0,
        )
