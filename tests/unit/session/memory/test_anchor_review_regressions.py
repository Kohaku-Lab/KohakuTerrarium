"""PR #605 review: event pagination, peer provenance, and cursor eviction."""

import re
from types import SimpleNamespace

import numpy as np
import pytest

import kohakuterrarium.builtins.tools.search_memory as memory_tool
from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.session.memory.async_search import LiveSessionMemory
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.navigation import MAX_ANCHORS
from kohakuterrarium.session.memory.source_events import sourced_events
from kohakuterrarium.session.store import SessionStore


def _ctx(store, agent="alice"):
    return ToolContext(
        agent_name=agent,
        session=Session(key="test"),
        agent=SimpleNamespace(session_store=store, config=None),
        working_dir=None,
    )


def _anchor_from(output: str) -> str:
    return re.search(r"^anchor: ([A-Za-z0-9_-]+)$", output, re.M).group(1)


def _close(store, context):
    live = getattr(context.session, "_memory", None)
    if live is not None:
        live.close()
    store.close(update_status=False)


@pytest.mark.parametrize("query_part", ["user", "second"])
async def test_context_pages_complete_text_event_not_fts_paragraph(
    tmp_path, query_part
):
    store = SessionStore(tmp_path / f"text-{query_part}.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    long_text = (
        "FIRST_PARAGRAPH " + "a" * 940 + "\n\n" + "SECOND_PART_MARKER " + "b" * 500
    )
    store.append_event("alice", "user_input", {"content": "FOCUS_USER_MARKER"})
    store.append_event("alice", "text", {"content": long_text})
    context = _ctx(store)
    tool = SearchMemoryTool()
    try:
        query = "FOCUS_USER_MARKER" if query_part == "user" else "SECOND_PART_MARKER"
        result = await tool.execute({"query": query, "mode": "fts"}, context=context)
        assert result.error is None
        anchor = _anchor_from(result.output)

        first_window = await tool.execute(
            {
                "anchor": anchor,
                "before": 0,
                "after": 1 if query_part == "user" else 0,
                "offset": 0,
            },
            context=context,
        )
        assert first_window.error is None
        live = context.session._memory
        window = await live.read_context(
            anchor, before=0, after=1 if query_part == "user" else 0, offset=0
        )
        event = next(
            item
            for item in window.events
            if item.role == ("after" if query_part == "user" else "match")
        )
        assert event.total == len(long_text)
        assert event.start == 0 and event.end == 900
        assert event.truncated
        assert event.content == long_text[:900]

        # Offset is relative to the whole original event, not one paragraph.
        next_page = await live.read_context(event.anchor, before=0, after=0, offset=900)
        page = next(item for item in next_page.events if item.role == "match")
        assert page.total == len(long_text)
        assert page.content == long_text[900:1800]
        assert "SECOND_PART_MARKER" in page.content
        assert page.end == len(long_text)
    finally:
        _close(store, context)


@pytest.mark.parametrize("mode", ["fts", "semantic"])
async def test_peer_hits_receive_anchors_without_indexing_unrelated_agents(
    tmp_path, monkeypatch, mode
):
    path = tmp_path / "peers.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob", "carol"])
    for agent, content in (
        ("alice", "ALICE_ONLY_CONTENT"),
        ("bob", "SEARCHABLE_BOB_PEER"),
        ("carol", "CAROL_NOT_RELEVANT"),
    ):
        store.append_event(agent, "user_input", {"content": content})

    class PeerEmbedder:
        dimensions = 4

        def encode(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

        def encode_one(self, query):
            return np.ones(4, dtype=np.float32)

    index = SessionMemory(str(path), PeerEmbedder() if mode == "semantic" else None)
    try:
        for agent in ("alice", "bob", "carol"):
            index.index_events(agent, sourced_events(store, agent))
    finally:
        index.close()
        store.close(update_status=False)

    reopened = SessionStore(path)
    context = _ctx(reopened, "alice")
    observed = []
    original = LiveSessionMemory._index_keywords

    def witness(self, names):
        observed.extend(names)
        return original(self, names)

    monkeypatch.setattr(LiveSessionMemory, "_index_keywords", witness)
    monkeypatch.setattr(memory_tool, "create_embedder", lambda _config: PeerEmbedder())
    try:
        tool = SearchMemoryTool()
        result = await tool.execute(
            {
                "query": "SEARCHABLE_BOB_PEER",
                "mode": mode,
                "agent": "bob" if mode == "semantic" else None,
            },
            context=context,
        )
        assert result.error is None
        assert "SEARCHABLE_BOB_PEER" in result.output
        assert "UNVERIFIABLE_SOURCE" not in result.output
        anchor = _anchor_from(result.output)
        window = await tool.execute(
            {"anchor": anchor, "before": 0, "after": 0}, context=context
        )
        assert window.error is None
        assert "SEARCHABLE_BOB_PEER" in window.output
        assert observed.count("bob") == 1
        assert observed.count("carol") == 0

        again = await tool.execute(
            {
                "query": "SEARCHABLE_BOB_PEER",
                "mode": mode,
                "agent": "bob" if mode == "semantic" else None,
            },
            context=context,
        )
        assert again.error is None and _anchor_from(again.output)
        assert observed.count("bob") == 1

        # Certification is process-local, but cannot remain true after the
        # peer's physical event frontier advances in the same live Store.
        reopened.append_event(
            "bob", "user_input", {"content": "BOB_APPENDED_AFTER_CERT"}
        )
        refreshed = await tool.execute(
            {
                "query": "SEARCHABLE_BOB_PEER",
                "mode": mode,
                "agent": "bob" if mode == "semantic" else None,
            },
            context=context,
        )
        assert refreshed.error is None and _anchor_from(refreshed.output)
        assert observed.count("bob") == 2
        assert observed.count("carol") == 0
    finally:
        _close(reopened, context)


async def test_full_anchor_cache_expansion_protects_active_cursor(tmp_path):
    store = SessionStore(tmp_path / "cache.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event("alice", "user_input", {"content": "ANCHOR_CACHE_FOCUS"})
    store.append_event("alice", "text", {"content": "VISIBLE_CACHE_NEIGHBOR"})
    context = _ctx(store)
    tool = SearchMemoryTool()
    try:
        result = await tool.execute(
            {"query": "ANCHOR_CACHE_FOCUS", "mode": "fts"}, context=context
        )
        assert result.error is None
        active = _anchor_from(result.output)
        nav = context.session._memory._navigator
        location = nav._anchors[active]
        for _ in range(MAX_ANCHORS - 1):
            nav._remember(location)
        assert len(nav._anchors) == MAX_ANCHORS
        assert next(iter(nav._anchors)) == active

        window = await tool.execute(
            {"anchor": active, "before": 0, "after": 1}, context=context
        )
        assert window.error is None, window.error
        assert "VISIBLE_CACHE_NEIGHBOR" in window.output
        assert active in nav._anchors
        assert len(nav._anchors) == MAX_ANCHORS
    finally:
        _close(store, context)


async def test_peer_certification_failure_retains_ordinary_search(
    tmp_path, monkeypatch
):
    """Failure in a *new* peer proof step must not turn Search into an error."""
    path = tmp_path / "peer-fault.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob"])
    store.append_event("alice", "user_input", {"content": "ALICE_VALID_LOCAL"})
    store.append_event("bob", "user_input", {"content": "BOB_VALID_OLD_INDEX"})
    original_index = SessionMemory(str(path))
    try:
        for name in ("alice", "bob"):
            original_index.index_events(name, sourced_events(store, name))
    finally:
        original_index.close()
        store.close(update_status=False)

    reopened = SessionStore(path)
    context = _ctx(reopened, "alice")
    original = LiveSessionMemory._index_keywords

    def fail_peer_refresh(self, names):
        if "bob" in names:
            raise RuntimeError("peer namespace became unreadable")
        return original(self, names)

    monkeypatch.setattr(LiveSessionMemory, "_index_keywords", fail_peer_refresh)
    try:
        result = await SearchMemoryTool().execute(
            {"query": "BOB_VALID_OLD_INDEX", "mode": "fts"}, context=context
        )
        assert result.error is None, result.error
        assert "BOB_VALID_OLD_INDEX" in result.output
        assert "Anchors unavailable (UNVERIFIABLE_SOURCE)" in result.output
        assert not re.search(r"^anchor:", result.output, re.M)
    finally:
        _close(reopened, context)


@pytest.mark.parametrize(
    "marker",
    ["UNICODE_TAIL_MARKER", "unicode_tail_marker", "UniCode_Tail_Marker"],
)
async def test_focused_unicode_preview_uses_original_source_offsets(tmp_path, marker):
    """Case-insensitive matching must not use positions from expanded lowercase."""
    store = SessionStore(tmp_path / "unicode-focus.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    prefix = "İSTANBUL İLE İZMİR İÇİN BİLGİ. " * 40
    raw_text = prefix + marker
    store.append_event("alice", "user_input", {"content": "START_OF_HISTORY"})
    store.append_event("alice", "text", {"content": raw_text})
    context = _ctx(store)
    tool = SearchMemoryTool()
    try:
        query = await tool.execute(
            {"query": "UNICODE_TAIL_MARKER", "mode": "fts"},
            context=context,
        )
        assert query.error is None, query.error
        anchor = _anchor_from(query.output)

        focused = await tool.execute(
            {"anchor": anchor, "before": 0, "after": 0}, context=context
        )
        assert focused.error is None, focused.error
        window = await context.session._memory.read_context(anchor, before=0, after=0)
        event = window.events[0]
        original_match = len(prefix)
        assert original_match > 900
        assert len(raw_text.lower()) > len(raw_text)
        assert event.total == len(raw_text)
        assert 0 <= event.start <= original_match < event.end <= len(raw_text)
        assert marker in event.content
        assert event.content == raw_text[event.start : event.end]

        explicit = await context.session._memory.read_context(
            anchor, before=0, after=0, offset=0
        )
        assert explicit.events[0].start == 0
        assert explicit.events[0].content == raw_text[:900]
    finally:
        _close(store, context)
