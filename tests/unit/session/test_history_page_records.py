"""Physical history reads and detail validation."""

import json

import pytest

from dataclasses import replace

from kohakuterrarium.errors import ConflictError, NotFoundError
from kohakuterrarium.session.history_records import history_detail
from kohakuterrarium.session import history_paging as hp
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.history import history_page_from_store


@pytest.fixture()
def store(tmp_path):
    store = SessionStore(tmp_path / "detail.kohakutr")
    yield store
    store.close()


def test_channel_values_are_read_only_for_page(store, monkeypatch):
    for index in range(10):
        store.save_channel_message("room", {"content": str(index), "message_id": "dup"})
    monkeypatch.setattr(
        store, "get_channel_messages", lambda *_: pytest.fail("full read")
    )
    page = history_page_from_store(store, "s", "s", "ch:room", limit=2)
    assert [item["content"] for item in page["messages"]] == ["8", "9"]
    assert len({item["_history_key"] for item in page["messages"]}) == 2
    assert hp.decode_cursor(page["history_page"]["before"]).ref == "room:m000008"


def test_snapshot_fallback_only_for_initial_request(store):
    store.save_conversation("ag", [{"role": "user", "content": "snapshot"}])
    page = history_page_from_store(store, "s", "s", "ag", limit=1)
    assert page["history_page"]["stream"] == "snapshot"
    assert page["messages"][0]["content"] == "snapshot"
    continuation = history_page_from_store(
        store,
        "s",
        "s",
        "ag",
        stream="snapshot",
        limit=1,
        before=page["history_page"]["before"],
        history_id=page["history_page"]["history_id"],
    )
    assert continuation["messages"] == []
    stale = history_page_from_store(store, "s", "s", "ag", history_id="stale")
    assert stale["history_page"]["stream"] == "events"
    assert stale["history_page"]["reset_required"]
    assert stale["messages"] == []


def test_complete_envelope_byte_bound_and_huge_routing_field(store, monkeypatch):
    monkeypatch.setattr(hp, "MAX_PAGE_BYTES", 2400)
    store.append_event("ag", "tool_result", {"output": "x" * 5000, "name": "n" * 5000})
    page = history_page_from_store(store, "s", "s" * 100, "ag", limit=2)
    assert page["events"], "oversized routing data must not silently drop the record"
    assert page["events"][0]["_history_truncated"]
    assert len(json.dumps(page, separators=(",", ":")).encode()) <= 2400


def test_event_prefix_does_not_include_another_agent(store):
    store.append_event("ag", "text", {"content": "mine"})
    store.append_event("ag:evil", "text", {"content": "secret"})
    page = hp.page_events(store, "ag", session_id="s")
    assert [item["content"] for item in page["items"]] == ["mine"]


@pytest.mark.parametrize(
    "stream,target", [("events", "ag"), ("snapshot", "ag"), ("channel", "ch:room")]
)
def test_full_detail_roundtrip_and_stale_record(store, monkeypatch, stream, target):
    monkeypatch.setattr(hp, "MAX_PAGE_BYTES", 2400)
    content = "漢字" * 10000
    if stream == "events":
        store.append_event(target, "text", {"content": content})
    elif stream == "channel":
        store.save_channel_message("room", {"content": content})
    else:
        store.save_conversation(target, [{"role": "user", "content": content}])
    page = history_page_from_store(store, "s", "s", target, stream=stream)
    item = (page["events"] or page["messages"])[0]
    kwargs = dict(
        session_id="s",
        stream=stream,
        ref=item["_history_detail"],
        history_id=page["history_page"]["history_id"],
    )
    assert history_detail(store, target, **kwargs)["record"]["content"] == content
    assert (
        history_detail(store, target, **kwargs)["record"]["_history_key"]
        == item["_history_key"]
    )
    with pytest.raises(ConflictError):
        history_detail(store, target, **{**kwargs, "history_id": "foreign"})
    with pytest.raises(hp.HistoryPagingError):
        history_detail(store, "ch:other" if stream == "channel" else "other", **kwargs)
    cursor = hp.decode_cursor(kwargs["ref"])
    for bad_ref in ("../secret", "other:e000000", "-1"):
        with pytest.raises(hp.HistoryPagingError):
            history_detail(
                store,
                target,
                **{**kwargs, "ref": hp.encode_cursor(replace(cursor, ref=bad_ref))},
            )
    if stream == "snapshot":
        store.save_conversation(target, [{"role": "user", "content": "changed"}])
    else:
        vault = store.events if stream == "events" else store.channels
        vault[cursor.ref] = {"content": "changed"}
    with pytest.raises(ConflictError):
        history_detail(store, target, **kwargs)
    if stream == "snapshot":
        store.save_conversation(target, [])
    else:
        del vault[cursor.ref]
    with pytest.raises(ConflictError if stream == "snapshot" else NotFoundError):
        history_detail(store, target, **kwargs)


def test_channel_append_and_deleted_cursor_are_stable(store):
    for i in range(4):
        store.save_channel_message("room", {"content": str(i), "message_id": "dup"})
    page = history_page_from_store(store, "s", "s", "ch:room", limit=2)
    hp_meta = page["history_page"]
    store.save_channel_message("room", {"content": "4"})
    newer = history_page_from_store(
        store,
        "s",
        "s",
        "ch:room",
        after=hp_meta["after"],
        history_id=hp_meta["history_id"],
    )
    assert [item["content"] for item in newer["messages"]] == ["4"]
    del store.channels[hp.decode_cursor(hp_meta["after"]).ref]
    stale = history_page_from_store(store, "s", "s", "ch:room", after=hp_meta["after"])
    assert stale["history_page"]["reset_required"]
    assert stale["messages"] == []


def test_snapshot_middle_replacement_resets_with_unchanged_anchor(store):
    store.save_conversation("ag", [{"content": text} for text in "ABCD"])
    page = history_page_from_store(store, "s", "s", "ag", limit=1)
    store.save_conversation("ag", [{"content": text} for text in "AXYD"])
    older = history_page_from_store(
        store,
        "s",
        "s",
        "ag",
        stream="snapshot",
        limit=2,
        before=page["history_page"]["before"],
        history_id=page["history_page"]["history_id"],
    )
    assert older["history_page"]["reset_required"]
    assert older["messages"] == []
    with pytest.raises(ConflictError):
        history_detail(
            store,
            "ag",
            session_id="s",
            stream="snapshot",
            ref=page["history_page"]["after"],
            history_id=page["history_page"]["history_id"],
        )


def test_forged_unavailable_fingerprint_does_not_bypass_replacement(store):
    store.append_event("ag", "text", {"content": "old"})
    page = hp.page_events(store, "ag", session_id="s")
    cursor = hp.decode_cursor(page["history_page"]["after"])
    store.events[cursor.ref] = {"content": "replacement"}
    with pytest.raises(ConflictError):
        history_detail(
            store,
            "ag",
            session_id="s",
            stream="events",
            ref=hp.encode_cursor(replace(cursor, fingerprint="u:fake")),
            history_id=page["history_page"]["history_id"],
        )


def test_malformed_and_wrong_target_cursor_are_bad_requests(store):
    with pytest.raises(hp.HistoryPagingError):
        hp.page_events(store, "ag", session_id="s", before="bad")
    cursor = hp.encode_cursor(hp.PageCursor("events", "other", "other:e000000", "f"))
    with pytest.raises(hp.HistoryPagingError):
        hp.page_events(store, "ag", session_id="s", before=cursor)
