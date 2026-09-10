"""Unit tests for :mod:`kohakuterrarium.session.history_paging`."""

from __future__ import annotations

import json

import pytest

from kohakuterrarium.session import history_paging as hp
from kohakuterrarium.session.history_paging import (
    HistoryPagingError,
    decode_cursor,
    encode_cursor,
    page_channels,
    page_events,
    page_snapshot,
)
from kohakuterrarium.session.store import SessionStore


@pytest.fixture()
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    s.close()


def _events(store, content):
    for text in content:
        store.append_event("ag", "text", {"content": text})


def _cursor(history_page, which):
    value = history_page[which]
    assert value is not None
    return value


def _walk_oldest(store, limit=2):
    """Walk back to the oldest page; returns (ordered keys, oldest page)."""
    page = page_events(store, "ag", session_id="g", limit=limit)
    ordered = [item["_history_key"] for item in page["items"]]
    before = _cursor(page["history_page"], "before")
    last = page
    while True:
        page = page_events(store, "ag", session_id="g", limit=limit, before=before)
        if not page["items"]:
            break
        ordered = [item["_history_key"] for item in page["items"]] + ordered
        before = _cursor(page["history_page"], "before")
        last = page
    return ordered, last


def test_tail_page_returns_newest_bounded_slice(store):
    _events(store, ["c0", "c1", "c2", "c3", "c4"])
    page = page_events(store, "ag", session_id="g", limit=2)
    assert [item["content"] for item in page["items"]] == ["c3", "c4"]
    hp_meta = page["history_page"]
    assert hp_meta["version"] == 1
    assert hp_meta["stream"] == "events"
    assert hp_meta["has_older"] is True
    assert hp_meta["has_newer"] is False
    assert hp_meta["reset_required"] is False
    # Cursors are exclusive and point at the oldest/newest raw items.
    assert decode_cursor(hp_meta["before"]).ref == "ag:e000003"
    assert decode_cursor(hp_meta["after"]).ref == "ag:e000004"


def test_event_page_never_includes_conversation_snapshot(store):
    store.save_conversation("ag", [{"role": "user", "content": "snap"}])
    _events(store, ["c0"])
    page = page_events(store, "ag", session_id="g", limit=5)
    # Paged events are physical records, not the snapshot messages.
    assert [item["content"] for item in page["items"]] == ["c0"]
    assert "role" not in page["items"][0]


def test_complete_before_traversal_has_no_holes_or_repeats(store):
    _events(store, ["c0", "c1", "c2", "c3", "c4"])
    page = page_events(store, "ag", session_id="g", limit=2)
    seen = [item["_history_key"] for item in page["items"]]
    before = _cursor(page["history_page"], "before")
    while True:
        page = page_events(store, "ag", session_id="g", limit=2, before=before)
        if not page["items"]:
            break
        seen = [item["_history_key"] for item in page["items"]] + seen
        before = _cursor(page["history_page"], "before")
    assert seen == [f"events:ag:ag:e00000{i}" for i in range(5)]
    assert len(set(seen)) == len(seen)


def test_complete_after_traversal_from_head(store):
    _events(store, ["c0", "c1", "c2", "c3", "c4"])
    _ordered, oldest = _walk_oldest(store, limit=2)
    after = _cursor(oldest["history_page"], "after")
    seen = [item["_history_key"] for item in oldest["items"]]
    while True:
        page = page_events(store, "ag", session_id="g", limit=2, after=after)
        if not page["items"]:
            break
        seen += [item["_history_key"] for item in page["items"]]
        after = _cursor(page["history_page"], "after")
    assert seen == [f"events:ag:ag:e00000{i}" for i in range(5)]
    assert len(set(seen)) == len(seen)


def test_physical_key_order_not_event_id_order(store):
    # Mirrored/imported ids are not monotonic with the physical key order.
    store.append_event("ag", "text", {"content": "first", "event_id": 100})
    store.append_event("ag", "text", {"content": "second", "event_id": 1})
    store.append_event("ag", "text", {"content": "third", "event_id": 50})
    page = page_events(store, "ag", session_id="g", limit=3)
    # Ordered by the physical numeric suffix, not by event_id.
    assert [item["content"] for item in page["items"]] == [
        "first",
        "second",
        "third",
    ]
    assert [item["event_id"] for item in page["items"]] == [100, 1, 50]


def test_events_carry_stable_history_key(store):
    _events(store, ["c0"])
    page = page_events(store, "ag", session_id="g", limit=5)
    assert page["items"][0]["_history_key"] == "events:ag:ag:e000000"


def test_history_id_survives_append(store):
    page1 = page_events(store, "ag", session_id="g", limit=5)
    hid = page1["history_page"]["history_id"]
    _events(store, ["new"])
    page2 = page_events(store, "ag", session_id="g", limit=5, history_id=hid)
    assert page2["history_page"]["reset_required"] is False
    assert page2["history_page"]["history_id"] == hid


def test_wrong_history_id_triggers_reset(store):
    _events(store, ["c0"])
    page = page_events(store, "ag", session_id="g", limit=5, history_id="stale")
    assert page["history_page"]["reset_required"] is True
    assert page["items"] == []


def test_malformed_cursor_triggers_reset(store):
    _events(store, ["c0", "c1"])
    with pytest.raises(HistoryPagingError):
        page_events(store, "ag", session_id="g", limit=5, before="not-a-valid-cursor!!")


def test_wrong_target_cursor_triggers_reset(store):
    _events(store, ["c0", "c1"])
    bad = encode_cursor(
        hp.PageCursor("events", "other-agent", "ag:e000001", "deadbeef")
    )
    with pytest.raises(HistoryPagingError):
        page_events(store, "ag", session_id="g", limit=5, before=bad)


def test_changed_head_cursor_triggers_reset(store):
    # A fingerprint mismatch (row content changed) forces reset_required.
    _events(store, ["c0", "c1", "c2"])
    page = page_events(store, "ag", session_id="g", limit=2)
    original_cursor = page["history_page"]["before"]
    ref = decode_cursor(original_cursor).ref
    store.events[ref] = {"content": "mutated", "type": "text"}
    store.events.flush_cache()
    reset = page_events(store, "ag", session_id="g", limit=2, before=original_cursor)
    assert reset["history_page"]["reset_required"] is True
    assert reset["items"] == []


def test_missing_cursor_key_triggers_reset(store):
    _events(store, ["c0", "c1", "c2"])
    page = page_events(store, "ag", session_id="g", limit=2)
    cursor = page["history_page"]["before"]
    ref = decode_cursor(cursor).ref
    del store.events[ref]
    store.events.flush_cache()
    reset = page_events(store, "ag", session_id="g", limit=2, before=cursor)
    assert reset["history_page"]["reset_required"] is True


def test_before_and_after_mutually_exclusive(store):
    _events(store, ["c0", "c1"])
    page = page_events(store, "ag", session_id="g", limit=2)
    with pytest.raises(HistoryPagingError):
        page_events(
            store,
            "ag",
            session_id="g",
            limit=2,
            before=page["history_page"]["before"],
            after=page["history_page"]["after"],
        )


def test_limit_clamped_to_max(store):
    _events(store, ["c0", "c1"])
    page = page_events(store, "ag", session_id="g", limit=10_000)
    # Clamped to MAX_LIMIT; still returns everything available.
    assert len(page["items"]) == 2
    assert page["history_page"]["after"] is not None


def test_nonpositive_limit_rejected(store):
    _events(store, ["c0"])
    with pytest.raises(HistoryPagingError):
        page_events(store, "ag", session_id="g", limit=0)
    with pytest.raises(HistoryPagingError):
        page_events(store, "ag", session_id="g", limit=-1)


def test_oversize_records_are_truncated_previews(store):
    huge = "x" * 3000
    for _ in range(3):
        store.append_event("ag", "tool_result", {"output": huge, "name": "tool_a"})
    old = hp.MAX_PAGE_BYTES
    hp.MAX_PAGE_BYTES = 3100
    try:
        page = page_events(store, "ag", session_id="g", limit=3)
    finally:
        hp.MAX_PAGE_BYTES = old
    assert page["items"]
    for item in page["items"]:
        assert item["_history_truncated"] is True
        # Routing metadata preserved; content is a bounded preview.
        assert item["name"] == "tool_a"
        assert item["type"] == "tool_result"
        assert item["_history_key"] != ""
        assert len(item["output"]) <= 2001
        assert "output_preview" in item
    assert _page_bytes(page) <= hp.MAX_PAGE_BYTES


def test_oversize_page_never_exceeds_hard_byte_bound(store):
    huge = "y" * 200_000
    for _ in range(50):
        store.append_event("ag", "text", {"content": huge})
    old = hp.MAX_PAGE_BYTES
    hp.MAX_PAGE_BYTES = 2048
    try:
        page = page_events(store, "ag", session_id="g", limit=400)
        budget = hp.MAX_PAGE_BYTES
    finally:
        hp.MAX_PAGE_BYTES = old
    assert len(page["items"]) > 0
    assert all("_history_truncated" in item for item in page["items"])
    assert _page_bytes(page) <= budget


def test_channel_page_preserves_message_id_and_never_fabricates_event_id():
    messages = [
        {"message_id": "m-1", "sender": "a", "content": "one", "timestamp": "10:00"},
        {"message_id": "m-2", "sender": "b", "content": "two", "timestamp": "10:01"},
        {"message_id": "m-3", "sender": "c", "content": "three", "timestamp": "10:02"},
    ]
    page = page_channels(messages, session_id="g", channel="room", limit=2)
    assert [item["message_id"] for item in page["items"]] == ["m-2", "m-3"]
    for item in page["items"]:
        assert "event_id" not in item
        assert "timestamp" in item
        assert item["_history_key"].startswith("channel:room:")


def test_channel_page_stable_key_and_no_repeat_across_pages():
    messages = [
        {
            "message_id": f"m-{i}",
            "sender": "a",
            "content": f"c{i}",
            "timestamp": "10:00",
        }
        for i in range(5)
    ]
    page = page_channels(messages, session_id="g", channel="room", limit=2)
    seen = [item["_history_key"] for item in page["items"]]
    before = _cursor(page["history_page"], "before")
    while True:
        page = page_channels(
            messages, session_id="g", channel="room", limit=2, before=before
        )
        if not page["items"]:
            break
        seen = [item["_history_key"] for item in page["items"]] + seen
        before = _cursor(page["history_page"], "before")
    assert len(set(seen)) == len(seen)
    assert page["history_page"]["stream"] == "channel"


def test_snapshot_page_bounded_items_with_absolute_index_identity():
    messages = [{"role": "user", "content": f"u{i}"} for i in range(5)]
    page = page_snapshot(messages, session_id="g", target="ag", limit=2)
    assert [item["content"] for item in page["items"]] == ["u3", "u4"]
    before = _cursor(page["history_page"], "before")
    page2 = page_snapshot(messages, session_id="g", target="ag", limit=2, before=before)
    assert [item["content"] for item in page2["items"]] == ["u1", "u2"]
    assert page["history_page"]["stream"] == "snapshot"


def test_live_metadata_passed_through(store):
    _events(store, ["c0"])
    page = page_events(
        store,
        "ag",
        session_id="g",
        limit=5,
        is_processing=True,
        live_job_ids=("job-1",),
    )
    assert page["is_processing"] is True
    assert page["live_job_ids"] == ["job-1"]


def test_cursor_round_trip():
    cursor = hp.PageCursor("events", "ag", "ag:e000042", "deadbeef1234")
    decoded = decode_cursor(encode_cursor(cursor))
    assert decoded == cursor


def test_decode_malformed_cursor_raises():
    with pytest.raises(HistoryPagingError):
        decode_cursor("!!not-base64!!")
    with pytest.raises(HistoryPagingError):
        decode_cursor("")


# ---------------------------------------------------------------------------
# Red tests for the previously-uncaught correctness bugs.
# ---------------------------------------------------------------------------


class _FakeEvents:
    """KVault-shaped stub that can mark individual refs as unreadable."""

    def __init__(self, records):
        self._records = records

    def flush_cache(self):
        pass

    def keys(self, prefix=None, limit=None):
        prefix = prefix or ""
        return [
            k.encode("utf-8") for k in sorted(self._records) if k.startswith(prefix)
        ]

    def __getitem__(self, ref):
        value = self._records[ref]
        if value is _UNREADABLE:
            raise KeyError(ref)
        return value


class _FakeStore:
    def __init__(self, records):
        self.events = _FakeEvents(records)


class _Unreadable:
    def __repr__(self):
        return "<unreadable>"


_UNREADABLE = _Unreadable()


def _page_bytes(page):
    return len(json.dumps(page, separators=(",", ":"), default=str).encode("utf-8"))


def test_byte_bound_page_payload_with_many_records(store):
    # Many records whose cumulative body would blow past the budget must be
    # terminated by actual bytes, not record count.
    for i in range(40):
        store.append_event("ag", "text", {"content": "z" * 4000})
    old = hp.MAX_PAGE_BYTES
    hp.MAX_PAGE_BYTES = 20_000
    try:
        page = page_events(store, "ag", session_id="g", limit=400)
    finally:
        hp.MAX_PAGE_BYTES = old
    assert page["items"]
    assert _page_bytes(page) <= hp.MAX_PAGE_BYTES


def test_byte_bound_nested_args_and_multimodal_content(store):
    # A single record with deeply nested dict args / multimodal (list-of-dict)
    # content must be un-bounded and the whole page stay under budget.
    huge_payload = {
        "type": "tool",
        "name": "search",
        "args": {
            "filters": [{"k": "v" * 60_000}],
            "nested": {"deep": {"deeper": ["x" * 60_000, {"y": "z" * 60_000}]}},
        },
        "content": [
            {"type": "image", "data": "b64" * 20_000},
            {"type": "text", "text": "w" * 40_000},
        ],
    }
    store.append_event("ag", "tool_result", huge_payload)
    old = hp.MAX_PAGE_BYTES
    hp.MAX_PAGE_BYTES = 30_000
    try:
        page = page_events(store, "ag", session_id="g", limit=100)
    finally:
        hp.MAX_PAGE_BYTES = old
    assert page["items"]
    item = page["items"][0]
    assert item["_history_truncated"] is True
    assert item["_history_detail"] != ""
    # Routing identity retains the tool name/type.
    assert item["name"] == "search"
    assert item["type"] == "tool_result"
    assert _page_bytes(page) <= hp.MAX_PAGE_BYTES


def test_unreadable_boundary_cursor_is_consumed_and_traversable():
    # The unreadable record is represented explicitly (not silently skipped),
    # the before cursor references a consumed record, and paging back does not
    # reset because of a ref/fingerprint mismatch.
    records = {
        "ag:e000000": {"content": "c0", "type": "text"},
        "ag:e000001": _UNREADABLE,
        "ag:e000002": {"content": "c2", "type": "text"},
    }
    store = _FakeStore(records)
    page = page_events(store, "ag", session_id="g", limit=2)
    assert len(page["items"]) == 2
    # The unreadable record is surfaced as an explicit unavailable item.
    keys = [item["_history_key"] for item in page["items"]]
    assert "events:ag:ag:e000001" in keys
    unavail = [i for i in page["items"] if i.get("_history_unavailable")]
    assert len(unavail) == 1
    assert decode_cursor(unavail[0]["_history_detail"]).ref == "ag:e000001"

    before = page["history_page"]["before"]
    cursor = decode_cursor(before)
    # Cursor references an actual consumed (physically present) record.
    assert cursor.ref in records
    # Re-requesting the previous page resolves cleanly, not as a reset.
    older = page_events(store, "ag", session_id="g", limit=2, before=before)
    assert older["history_page"]["reset_required"] is False


def test_channel_duplicate_and_missing_message_ids_are_distinct():
    messages = [
        {"message_id": "dup", "sender": "a", "content": "c0"},
        {"message_id": "dup", "sender": "b", "content": "c1"},
        {"sender": "c", "content": "c2"},
    ]
    page = page_channels(messages, session_id="g", channel="room", limit=3)
    keys = [item["_history_key"] for item in page["items"]]
    assert len(set(keys)) == 3
    assert len(page["items"]) == 3
    # message_id is preserved where present and never fabricated otherwise.
    got_ids = [item.get("message_id") for item in page["items"]]
    assert "dup" in got_ids and got_ids.count("dup") == 2
    assert got_ids.count(None) == 1
    for item in page["items"]:
        assert "event_id" not in item


def test_encode_cursor_docstring_does_not_claim_tamper_evidence():
    # base64 is not tamper-evident; the doc must not claim it is.
    doc = hp.encode_cursor.__doc__ or ""
    assert "tamper" not in doc.lower()
    assert "opaque" in doc.lower() or "exclusive" in doc.lower()
