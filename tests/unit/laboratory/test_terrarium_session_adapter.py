"""Unit tests for :class:`TerrariumSessionAdapter`.

The adapter exposes worker-local session operations (``history``,
``search``, ``stores``, ``resume``) over the ``terrarium.session`` APP
namespace.  Tests drive it with a fake engine carrying real
:class:`SessionStore` instances so the read ops verify actual event
data, not stubbed shapes.
"""

from pathlib import Path

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_session import (
    TerrariumSessionAdapter,
)
from kohakuterrarium.session.store import SessionStore


class _FakeNode:
    def __init__(self):
        self.handlers = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.handlers[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        return self.handlers.pop(ns, None) is not None


class _FakeEngine:
    """Engine double exposing only what the session adapter touches."""

    def __init__(self):
        self._session_stores: dict[str, SessionStore] = {}
        self._adopt_result: str | None = None
        self._adopt_calls: list[dict] = []

    async def adopt_session(self, path, *, pwd=None, llm_override=None):
        self._adopt_calls.append(
            {"path": path, "pwd": pwd, "llm_override": llm_override}
        )
        if self._adopt_result is None:
            raise RuntimeError("adopt not configured")
        return self._adopt_result


def _msg(type_, body, sender="ctrl"):
    return AppMessage(
        namespace="terrarium.session",
        type=type_,
        body=body,
        sender_node=sender,
        request_id=None,
        in_reply_to=None,
    )


@pytest.fixture
def _engine():
    return _FakeEngine()


@pytest.fixture
def _adapter(_engine):
    node = _FakeNode()
    adapter = TerrariumSessionAdapter(_engine, node)
    yield adapter
    for store in _engine._session_stores.values():
        store.close()


def _store_with_events(tmp_path: Path, name: str, agent: str, n: int):
    store = SessionStore(str(tmp_path / f"{name}.kohakutr"))
    for i in range(n):
        store.append_event(agent, "text", {"chunk": f"msg-{i}"})
    return store


# ── construction ────────────────────────────────────────────────


class TestConstruction:
    def test_registers_and_detaches(self, _engine):
        node = _FakeNode()
        adapter = TerrariumSessionAdapter(_engine, node)
        assert "terrarium.session" in node.handlers
        adapter.detach()
        assert "terrarium.session" in node.unregistered


# ── error mapping ───────────────────────────────────────────────


class TestErrorMapping:
    async def test_unknown_type_returns_structured_error(self, _adapter):
        out = await _adapter._dispatch(_msg("bogus", {}))
        assert out["error"]["kind"] == "unknown_type"
        assert "bogus" in out["error"]["message"]

    async def test_history_missing_session_id_is_invalid(self, _adapter):
        out = await _adapter._dispatch(_msg("history", {"agent": "alice"}))
        assert out["error"]["kind"] == "invalid"

    async def test_history_missing_agent_is_invalid(self, _adapter):
        out = await _adapter._dispatch(_msg("history", {"session_id": "s1"}))
        assert out["error"]["kind"] == "invalid"

    async def test_history_unknown_session_is_not_found(self, _adapter):
        out = await _adapter._dispatch(
            _msg("history", {"session_id": "ghost", "agent": "alice"})
        )
        assert out["error"]["kind"] == "not_found"

    async def test_search_missing_query_is_invalid(self, _adapter):
        out = await _adapter._dispatch(_msg("search", {"session_id": "s1"}))
        assert out["error"]["kind"] == "invalid"

    async def test_search_missing_session_id_is_invalid(self, _adapter):
        out = await _adapter._dispatch(_msg("search", {"query": "x"}))
        assert out["error"]["kind"] == "invalid"


# ── history ─────────────────────────────────────────────────────


class TestHistory:
    async def test_returns_all_events_for_agent(self, _adapter, _engine, tmp_path):
        _engine._session_stores["s1"] = _store_with_events(tmp_path, "s1", "alice", 3)
        out = await _adapter._dispatch(
            _msg("history", {"session_id": "s1", "agent": "alice"})
        )
        # Every appended event comes back, in order.
        chunks = [e["chunk"] for e in out["events"]]
        assert chunks == ["msg-0", "msg-1", "msg-2"]

    async def test_since_filters_to_newer_events(self, _adapter, _engine, tmp_path):
        store = _store_with_events(tmp_path, "s2", "alice", 4)
        _engine._session_stores["s2"] = store
        full = await _adapter._dispatch(
            _msg("history", {"session_id": "s2", "agent": "alice"})
        )
        cutoff = int(full["events"][1]["event_id"])
        out = await _adapter._dispatch(
            _msg(
                "history",
                {"session_id": "s2", "agent": "alice", "since": cutoff},
            )
        )
        # Only events with id strictly greater than the cutoff remain.
        assert all(int(e["event_id"]) > cutoff for e in out["events"])
        assert len(out["events"]) == 2

    async def test_limit_truncates_event_list(self, _adapter, _engine, tmp_path):
        _engine._session_stores["s3"] = _store_with_events(tmp_path, "s3", "alice", 5)
        out = await _adapter._dispatch(
            _msg(
                "history",
                {"session_id": "s3", "agent": "alice", "limit": 2},
            )
        )
        assert len(out["events"]) == 2
        assert out["events"][0]["chunk"] == "msg-0"

    async def test_history_isolated_per_agent(self, _adapter, _engine, tmp_path):
        store = SessionStore(str(tmp_path / "multi.kohakutr"))
        store.append_event("alice", "text", {"chunk": "a"})
        store.append_event("bob", "text", {"chunk": "b"})
        _engine._session_stores["s4"] = store
        out = await _adapter._dispatch(
            _msg("history", {"session_id": "s4", "agent": "alice"})
        )
        # Only alice's event — bob's is not leaked into the response.
        assert [e["chunk"] for e in out["events"]] == ["a"]


# ── search ──────────────────────────────────────────────────────


class TestSearch:
    async def test_search_returns_matching_hits(self, _adapter, _engine, tmp_path):
        store = SessionStore(str(tmp_path / "srch.kohakutr"))
        # ``text``-keyed event data is what the FTS index ingests.
        eid_fox, _ = store.append_event(
            "alice", "text", {"text": "the quick brown fox"}
        )
        store.append_event("alice", "text", {"text": "lazy dog sleeps"})
        _engine._session_stores["s5"] = store
        out = await _adapter._dispatch(
            _msg("search", {"session_id": "s5", "query": "fox", "k": 5})
        )
        # The FTS query surfaces the matching event by its metadata.
        assert isinstance(out["hits"], list)
        matched_ids = {h["meta"]["event_key"] for h in out["hits"]}
        assert eid_fox in matched_ids

    async def test_search_unknown_session_is_not_found(self, _adapter):
        out = await _adapter._dispatch(
            _msg("search", {"session_id": "ghost", "query": "x"})
        )
        assert out["error"]["kind"] == "not_found"


# ── stores ──────────────────────────────────────────────────────


class TestStores:
    async def test_stores_lists_attached_session_ids_sorted(
        self, _adapter, _engine, tmp_path
    ):
        _engine._session_stores["zeta"] = _store_with_events(tmp_path, "zeta", "a", 1)
        _engine._session_stores["alpha"] = _store_with_events(tmp_path, "alpha", "a", 1)
        out = await _adapter._dispatch(_msg("stores", {}))
        # The worker reports every live store id, sorted.
        assert out["session_ids"] == ["alpha", "zeta"]

    async def test_stores_empty_when_no_live_stores(self, _adapter):
        out = await _adapter._dispatch(_msg("stores", {}))
        assert out == {"session_ids": []}


# ── resume ──────────────────────────────────────────────────────


class TestResume:
    async def test_resume_missing_path_is_invalid(self, _adapter):
        out = await _adapter._dispatch(_msg("resume", {}))
        assert out["error"]["kind"] == "invalid"

    async def test_resume_nonexistent_file_is_not_found(self, _adapter):
        out = await _adapter._dispatch(
            _msg("resume", {"path": "/no/such/file.kohakutr"})
        )
        # FileNotFoundError is a KeyError sibling? No — it maps via the
        # generic Exception arm to ``session``; assert it surfaces as a
        # structured error rather than crashing the dispatcher.
        assert "error" in out
        assert out["error"]["kind"] in {"not_found", "session"}

    async def test_resume_adopts_file_and_returns_session_meta(
        self, _adapter, _engine, tmp_path
    ):
        # A real .kohakutr the worker can adopt.
        kohakutr = tmp_path / "saved.kohakutr"
        seed = SessionStore(str(kohakutr))
        seed.append_event("alice", "text", {"chunk": "hi"})
        seed.close()
        # Engine adopt yields a graph id; the adapter reads that store's
        # metadata back into the response.
        adopted = SessionStore(str(tmp_path / "adopted.kohakutr"))
        _engine._session_stores["g-new"] = adopted
        _engine._adopt_result = "g-new"
        out = await _adapter._dispatch(
            _msg(
                "resume",
                {
                    "path": str(kohakutr),
                    "pwd_override": "/work",
                    "llm_override": "gpt",
                },
            )
        )
        assert out["session_id"] == "g-new"
        assert isinstance(out["meta"], dict)
        # The override params were forwarded to engine.adopt_session.
        call = _engine._adopt_calls[0]
        assert call["pwd"] == "/work"
        assert call["llm_override"] == "gpt"

    async def test_resume_adopt_with_no_resulting_store_returns_empty_meta(
        self, _adapter, _engine, tmp_path
    ):
        kohakutr = tmp_path / "saved2.kohakutr"
        seed = SessionStore(str(kohakutr))
        seed.append_event("alice", "text", {"chunk": "hi"})
        seed.close()
        # adopt_session returns an id with no matching live store —
        # the adapter must still answer with an empty meta dict.
        _engine._adopt_result = "g-missing"
        out = await _adapter._dispatch(_msg("resume", {"path": str(kohakutr)}))
        assert out["session_id"] == "g-missing"
        assert out["meta"] == {}
