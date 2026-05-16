"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.turns`."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from kohakuterrarium.studio.persistence.viewer import turns as turns_mod


def _make_store(meta=None, attached_namespaces=None, rollups=None):
    store = MagicMock()
    store.load_meta.return_value = meta or {"agents": ["alice"]}
    store.discover_attached_agents.return_value = [
        {"namespace": ns} for ns in (attached_namespaces or [])
    ]
    return store


class TestBuildTurnsPayload:
    def test_aggregate_mode(self, monkeypatch):
        store = _make_store()
        monkeypatch.setattr(
            turns_mod,
            "aggregate_turn_rollups",
            lambda s: [{"turn_index": 0}, {"turn_index": 1}],
        )
        out = turns_mod.build_turns_payload(
            store,
            "x",
            agent=None,
            from_turn=None,
            to_turn=None,
            limit=10,
            offset=0,
            aggregate=True,
        )
        assert out["aggregate"] is True
        assert len(out["turns"]) == 2
        assert out["agent"] is None

    def test_per_agent_with_explicit(self, monkeypatch):
        store = _make_store({"agents": ["alice", "bob"]})
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [{"turn_index": 0, "agent": a}],
        )
        out = turns_mod.build_turns_payload(
            store, "x", agent="bob", from_turn=None, to_turn=None, limit=10, offset=0
        )
        assert out["agent"] == "bob"

    def test_per_agent_default_uses_viewer_default(self, monkeypatch):
        store = _make_store(
            {
                "agents": ["alice", "bob"],
                "viewer_default_agent": "bob",
            }
        )
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [{"turn_index": 0}],
        )
        out = turns_mod.build_turns_payload(
            store, "x", agent=None, from_turn=None, to_turn=None, limit=10, offset=0
        )
        assert out["agent"] == "bob"

    def test_per_agent_default_first_agent_fallback(self, monkeypatch):
        store = _make_store({"agents": ["alice", "bob"]})
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [{"turn_index": 0}],
        )
        out = turns_mod.build_turns_payload(
            store, "x", agent=None, from_turn=None, to_turn=None, limit=10, offset=0
        )
        assert out["agent"] == "alice"

    def test_per_agent_no_agents_raises(self):
        store = _make_store({"agents": []})
        with pytest.raises(HTTPException) as exc:
            turns_mod.build_turns_payload(
                store, "x", agent=None, from_turn=None, to_turn=None, limit=10, offset=0
            )
        assert exc.value.status_code == 404

    def test_per_agent_unknown_agent_raises(self):
        store = _make_store({"agents": ["alice"]})
        with pytest.raises(HTTPException) as exc:
            turns_mod.build_turns_payload(
                store,
                "x",
                agent="ghost",
                from_turn=None,
                to_turn=None,
                limit=10,
                offset=0,
            )
        assert exc.value.status_code == 404

    def test_attached_agent_namespace_accepted(self, monkeypatch):
        store = _make_store(
            {"agents": ["alice"]}, attached_namespaces=["alice:attached:tool"]
        )
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [{"turn_index": 0}],
        )
        out = turns_mod.build_turns_payload(
            store,
            "x",
            agent="alice:attached:tool",
            from_turn=None,
            to_turn=None,
            limit=10,
            offset=0,
        )
        assert out["agent"] == "alice:attached:tool"

    def test_from_turn_filter(self, monkeypatch):
        store = _make_store({"agents": ["alice"]})
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [
                {"turn_index": 0},
                {"turn_index": 1},
                {"turn_index": 2},
            ],
        )
        out = turns_mod.build_turns_payload(
            store,
            "x",
            agent="alice",
            from_turn=1,
            to_turn=None,
            limit=10,
            offset=0,
        )
        assert all(r["turn_index"] >= 1 for r in out["turns"])

    def test_to_turn_filter(self, monkeypatch):
        store = _make_store({"agents": ["alice"]})
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [
                {"turn_index": 0},
                {"turn_index": 5},
            ],
        )
        out = turns_mod.build_turns_payload(
            store,
            "x",
            agent="alice",
            from_turn=None,
            to_turn=2,
            limit=10,
            offset=0,
        )
        assert all(r["turn_index"] <= 2 for r in out["turns"])

    def test_pagination(self, monkeypatch):
        store = _make_store({"agents": ["alice"]})
        monkeypatch.setattr(
            turns_mod,
            "rollups_or_derived",
            lambda s, a: [{"turn_index": i} for i in range(10)],
        )
        out = turns_mod.build_turns_payload(
            store,
            "x",
            agent="alice",
            from_turn=None,
            to_turn=None,
            limit=3,
            offset=5,
        )
        assert len(out["turns"]) == 3
        assert out["turns"][0]["turn_index"] == 5
        assert out["total"] == 10
