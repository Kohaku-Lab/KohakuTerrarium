"""Saved-session paged history helper tests.

``history_page_from_store`` is the read path shared by the saved-history HTTP
route and the engine-owned live-store path: given an open ``SessionStore`` and a
target it returns a bounded, cursor-driven page reusing the session pager. This
is the piece that was NOT wired before: the saved ``/history/{target}`` route
only ever returned the full snapshot.
"""

from __future__ import annotations

import pytest
from kohakuvault import KVault

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.history.paging import HistoryPagingError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.history import (
    history_index_from_store,
    history_page_from_store,
)
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload


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
    _events(store, ["c0"])
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
    _events(store, ["c0"])
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
    _events(store, ["c0"])
    with pytest.raises(HistoryPagingError):
        history_page_from_store(
            store,
            session_id="s",
            session_name="s",
            target="ag",
            stream="events",
            limit=0,
        )


def test_index_viewer_agents_are_key_only_and_do_not_expand_history_targets(
    store, monkeypatch
):
    store.init_meta(
        "discovery",
        "terrarium",
        "/p",
        "/w",
        ["alice", "bob"],
        terrarium_channels=[{"name": "ops"}],
    )
    attached = "alice:attached:review:0"
    for name in ["alice", "bob", "worker", attached]:
        store.append_event(name, "user_input", {"content": name})
    store.save_channel_message("ops", {"sender": "alice", "content": "hello"})
    store.set_viewer_default_agent(attached)
    store.flush()
    reads = []
    original_get = KVault.get
    original_item = KVault.__getitem__
    original_many = KVault.get_many

    def get(vault, key, *args, **kwargs):
        if vault is store.events:
            reads.append(key)
        return original_get(vault, key, *args, **kwargs)

    def item(vault, key):
        if vault is store.events:
            reads.append(key)
        return original_item(vault, key)

    def many(vault, keys, *args, **kwargs):
        if vault is store.events:
            reads.extend(keys)
        return original_many(vault, keys, *args, **kwargs)

    monkeypatch.setattr(KVault, "get", get)
    monkeypatch.setattr(KVault, "__getitem__", item)
    monkeypatch.setattr(KVault, "get_many", many)
    index = history_index_from_store(store, "discovery")
    assert index["viewer_agents"] == [attached, "alice", "bob", "worker"]
    assert index["targets"] == ["alice", "bob", "worker", "ch:ops"]
    assert index["meta"]["agents"] == ["alice", "bob", "worker"]
    assert reads == []
    assert (
        build_summary_payload(store, "discovery", None)["agents"]
        == index["viewer_agents"]
    )
    assert reads
    with pytest.raises(NotFoundError):
        history_page_from_store(
            store, session_id="s", session_name="s", target=attached
        )
    assert (
        history_page_from_store(
            store, session_id="s", session_name="s", target="ch:ops"
        )["messages"][0]["content"]
        == "hello"
    )


def test_saved_unknown_target_is_not_an_empty_page(store):
    _events(store, ["c0"])
    with pytest.raises(NotFoundError):
        history_page_from_store(
            store, session_id="s", session_name="s", target="missing", limit=5
        )
    with pytest.raises(NotFoundError):
        history_page_from_store(
            store, session_id="s", session_name="s", target="ch:missing", limit=5
        )
