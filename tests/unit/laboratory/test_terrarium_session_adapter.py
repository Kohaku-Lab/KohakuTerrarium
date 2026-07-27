"""Unit tests for :class:`TerrariumSessionAdapter`.

The adapter exposes worker-local session operations (``history``,
``search``, ``stores``, ``resume``) over the ``terrarium.session`` APP
namespace.  Tests drive it with a fake engine carrying real
:class:`SessionStore` instances so the read ops verify actual event
data, not stubbed shapes.
"""

from pathlib import Path
from types import SimpleNamespace

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
        self._creatures: dict[str, object] = {}

    async def adopt_session(self, path, *, pwd=None, llm=None):
        self._adopt_calls.append({"path": path, "pwd": pwd, "llm": llm})
        if self._adopt_result is None:
            raise RuntimeError("adopt not configured")
        return self._adopt_result

    def list_creatures(self):
        return list(self._creatures.values())


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


# ── resume op ───────────────────────────────────────────────────


class TestResumeOp:
    async def test_resume_reports_worker_side_pwd_exists(
        self, _adapter, _engine, tmp_path
    ):
        kohakutr = tmp_path / "s.kohakutr"
        store = SessionStore(str(kohakutr))
        missing = tmp_path / "gone"
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(missing),
            agents=["a"],
        )
        _engine._adopt_result = "sid1"
        _engine._session_stores["sid1"] = store
        _engine._creatures["cid-a"] = SimpleNamespace(
            creature_id="cid-a",
            name="alice",
            graph_id="sid1",
            is_running=True,
            is_privileged=False,
        )
        out = await _adapter._op_resume(
            {"path": str(kohakutr), "pwd_override": str(tmp_path)}
        )
        assert out["session_id"] == "sid1"
        assert out["session_path"] == str(kohakutr)
        # Evaluated on the worker (this process) — the saved dir does
        # not exist here, whatever the controller might think.
        assert out["pwd_exists"] is False
        assert _engine._adopt_calls[-1]["pwd"] == str(tmp_path)
        assert out["creatures"] == [
            {
                "creature_id": "cid-a",
                "name": "alice",
                "running": True,
                "is_privileged": False,
            }
        ]

    async def test_resume_pwd_exists_true_when_dir_present(
        self, _adapter, _engine, tmp_path
    ):
        kohakutr = tmp_path / "s2.kohakutr"
        store = SessionStore(str(kohakutr))
        store.init_meta(
            session_id="s2",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["a"],
        )
        _engine._adopt_result = "sid2"
        _engine._session_stores["sid2"] = store
        out = await _adapter._op_resume({"path": str(kohakutr)})
        assert out["pwd_exists"] is True

    async def test_set_lifecycle_updates_worker_store(self, _adapter, tmp_path):
        path = tmp_path / "lifecycle.kohakutr"
        store = SessionStore(path)
        store.init_meta("sid", "agent", "x", str(tmp_path), ["a"])
        store.close(update_status=False)

        result = _adapter._op_set_lifecycle(
            {
                "session_path": str(path),
                "conversation_open": False,
                "status": "completed",
            }
        )

        assert result["ok"] is True
        reopened = SessionStore.open_readonly(path)
        try:
            assert bool(reopened.meta["conversation_open"]) is False
            assert reopened.meta["status"] == "completed"
        finally:
            reopened.close(update_status=False)

    async def test_set_lifecycle_reuses_open_engine_store(
        self, _adapter, _engine, tmp_path, monkeypatch
    ):
        path = tmp_path / "live.kohakutr"
        store = SessionStore(path)
        store.init_meta("sid", "agent", "x", str(tmp_path), ["a"])
        _engine._session_stores["sid"] = store

        def fail_open(*args, **kwargs):
            raise AssertionError("must reuse the live engine store")

        monkeypatch.setattr(
            "kohakuterrarium.laboratory.adapters.terrarium_session.SessionStore",
            fail_open,
        )

        result = _adapter._op_set_lifecycle(
            {
                "session_path": str(path),
                "conversation_open": False,
                "status": "completed",
            }
        )

        assert result["ok"] is True
        assert bool(store.meta["conversation_open"]) is False
        assert store.meta["status"] == "completed"

    async def test_rollback_resume_removes_graph_members(
        self, _adapter, _engine, tmp_path
    ):
        _engine._creatures = {
            "one": SimpleNamespace(creature_id="one", graph_id="target"),
            "two": SimpleNamespace(creature_id="two", graph_id="target"),
            "other": SimpleNamespace(creature_id="other", graph_id="other"),
        }
        removed = []
        session_path = tmp_path / "rollback.kohakutr"
        store = SessionStore(session_path)
        store.init_meta("target", "agent", "x", str(tmp_path), ["one", "two"])
        _engine._session_stores = {"target": store}
        _engine.list_creatures = lambda: list(_engine._creatures.values())

        async def remove_creature(creature_id):
            assert "target" not in _engine._session_stores
            removed.append(creature_id)
            _engine._creatures.pop(creature_id)

        _engine.remove_creature = remove_creature

        result = await _adapter._op_rollback_resume({"graph_id": "target"})

        assert result == {"ok": True, "removed": ["one", "two"]}
        assert removed == ["two", "one"]
        assert list(_engine._creatures) == ["other"]
        assert session_path.exists() is False

    async def test_rollback_resume_resolves_graph_from_session_path(
        self, _adapter, _engine, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        session_path = tmp_path / "resume" / "rollback-by-path.kohakutr"
        session_path.parent.mkdir()
        store = SessionStore(session_path)
        store.init_meta("target", "agent", "x", str(tmp_path), ["one"])
        _engine._session_stores = {"target": store}
        _engine._adopt_result = "target"
        _engine._creatures = {
            "one": SimpleNamespace(creature_id="one", graph_id="target"),
        }
        _engine.list_creatures = lambda: list(_engine._creatures.values())
        removed = []

        async def remove_creature(creature_id):
            removed.append(creature_id)
            _engine._creatures.pop(creature_id)

        _engine.remove_creature = remove_creature

        await _adapter._op_resume(
            {"path": str(session_path), "resume_token": "resume-token"}
        )
        result = await _adapter._op_rollback_resume(
            {
                "session_path": str(session_path),
                "resume_token": "resume-token",
            }
        )

        assert result == {"ok": True, "removed": ["one"]}
        assert removed == ["one"]
        assert session_path.exists() is False

    async def test_path_rollback_does_not_remove_a_preexisting_active_store(
        self, _adapter, _engine, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        session_path = tmp_path / "resume" / "already-active.kohakutr"
        session_path.parent.mkdir()
        store = SessionStore(session_path)
        store.init_meta("target", "agent", "x", str(tmp_path), ["one"])
        _engine._session_stores = {"target": store}
        _engine._creatures = {
            "one": SimpleNamespace(creature_id="one", graph_id="target"),
        }
        _engine.list_creatures = lambda: list(_engine._creatures.values())

        with pytest.raises(ValueError, match="active session store"):
            await _adapter._op_rollback_resume(
                {
                    "session_path": str(session_path),
                    "resume_token": "unrecognized-attempt",
                }
            )

        assert list(_engine._creatures) == ["one"]
        assert _engine._session_stores == {"target": store}
        assert session_path.exists() is True

    def test_delete_transfer_refuses_an_active_store(
        self, _adapter, _engine, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        session_path = tmp_path / "resume" / "active.kohakutr"
        session_path.parent.mkdir()
        store = SessionStore(session_path)
        store.init_meta("target", "agent", "x", str(tmp_path), ["one"])
        _engine._session_stores = {"target": store}

        with pytest.raises(ValueError, match="active session store"):
            _adapter._op_delete_transfer({"session_path": str(session_path)})

        assert session_path.exists() is True

    def test_delete_transfer_rejects_a_path_outside_transfer_directory(
        self, _adapter, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))
        outside = tmp_path / "unrelated.kohakutr"
        outside.write_bytes(b"keep")

        with pytest.raises(ValueError, match="transfer directory"):
            _adapter._op_delete_transfer({"session_path": str(outside)})

        assert outside.read_bytes() == b"keep"


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
                    "llm": "gpt",
                },
            )
        )
        assert out["session_id"] == "g-new"
        assert isinstance(out["meta"], dict)
        # The override params were forwarded to engine.adopt_session.
        call = _engine._adopt_calls[0]
        assert call["pwd"] == "/work"
        assert call["llm"] == "gpt"

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
