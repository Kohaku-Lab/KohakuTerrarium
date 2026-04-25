"""Wave B §3.3 — per-turn rollup table.

Covers the new ``turn_rollup`` KVault table and the
``save_turn_rollup`` / ``get_turn_rollup`` helpers on ``SessionStore``.
Rows must round-trip across a close + reopen, and listing by agent
must return rows in ``turn_index`` order.
"""

import pytest

from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "rollup.kohakutr"
    store = SessionStore(path)
    store.init_meta(
        session_id="rollup",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    store.close()
    return path


class TestTurnRollupRoundTrip:
    def test_save_and_load_single_row(self, store_path):
        store = SessionStore(store_path)
        try:
            store.save_turn_rollup(
                "agent",
                0,
                {
                    "started_at": "2026-04-25T00:00:00Z",
                    "ended_at": "2026-04-25T00:00:05Z",
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "tokens_cached": 10,
                },
            )
            row = store.get_turn_rollup("agent", 0)
            assert row is not None
            assert row["tokens_in"] == 100
            assert row["tokens_cached"] == 10
            # Defaults wire agent + turn_index + cost_usd automatically.
            assert row["agent"] == "agent"
            assert row["turn_index"] == 0
            assert row["cost_usd"] is None
        finally:
            store.close()

    def test_survives_close_and_reopen(self, store_path):
        store = SessionStore(store_path)
        store.save_turn_rollup(
            "agent",
            3,
            {"tokens_in": 1000, "tokens_out": 200, "tokens_cached": 500},
        )
        store.close()

        reopened = SessionStore(store_path)
        try:
            row = reopened.get_turn_rollup("agent", 3)
            assert row is not None
            assert row["tokens_in"] == 1000
            assert row["tokens_cached"] == 500
        finally:
            reopened.close()

    def test_missing_row_returns_none(self, store_path):
        store = SessionStore(store_path)
        try:
            assert store.get_turn_rollup("agent", 99) is None
        finally:
            store.close()

    def test_list_rows_in_turn_order(self, store_path):
        store = SessionStore(store_path)
        try:
            # Insert out of order.
            for i in [2, 0, 1]:
                store.save_turn_rollup("agent", i, {"tokens_in": i * 10})
            rows = store.list_turn_rollups("agent")
            assert [r["turn_index"] for r in rows] == [0, 1, 2]
            assert [r["tokens_in"] for r in rows] == [0, 10, 20]
        finally:
            store.close()

    def test_agent_namespacing(self, store_path):
        store = SessionStore(store_path)
        try:
            store.save_turn_rollup("a1", 0, {"tokens_in": 1})
            store.save_turn_rollup("a2", 0, {"tokens_in": 2})
            assert store.get_turn_rollup("a1", 0)["tokens_in"] == 1
            assert store.get_turn_rollup("a2", 0)["tokens_in"] == 2
            assert len(store.list_turn_rollups("a1")) == 1
            assert len(store.list_turn_rollups("a2")) == 1
        finally:
            store.close()

    def test_cost_usd_is_optional_and_nullable(self, store_path):
        store = SessionStore(store_path)
        try:
            store.save_turn_rollup("agent", 0, {"tokens_in": 100})
            assert store.get_turn_rollup("agent", 0)["cost_usd"] is None
            store.save_turn_rollup("agent", 1, {"cost_usd": 0.12})
            assert store.get_turn_rollup("agent", 1)["cost_usd"] == 0.12
        finally:
            store.close()
