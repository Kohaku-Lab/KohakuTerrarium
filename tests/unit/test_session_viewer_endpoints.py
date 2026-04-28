"""Tests for the Session Viewer / Trace Viewer V1 endpoints.

Covers:

* ``SessionStore.subscribe`` / ``unsubscribe`` fan-out from
  ``append_event`` (the live-attach hook used by the WebSocket).
* ``GET /sessions/{n}/tree`` — standalone, with parent/child fork
  lineage, with attached agents.
* ``GET /sessions/{n}/summary`` — totals + hot-turn selection by cost
  fallback to tokens.
* ``GET /sessions/{n}/turns`` — pagination, range filter, missing agent.
* ``GET /sessions/{n}/events`` — type / turn / ts filters + cursor.

The WS endpoint relies on ``KohakuManager`` resolving a live store; that
plumbing is exercised in integration tests. Here we focus on the
read-only HTTP surface and the in-store pub-sub it depends on.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as persistence_store

from tests.unit._persistence_test_helpers import mount_session_routes

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def app_with_sessions(tmp_path: Path, monkeypatch):
    """FastAPI app mounted at ``/api/sessions`` against a tmp dir."""
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    mount_session_routes(app)
    return app, tmp_path


def _make_session(
    session_dir: Path,
    name: str,
    *,
    agents: list[str] | None = None,
    lineage: dict | None = None,
    forked_children: list[dict] | None = None,
) -> Path:
    """Seed a ``.kohakutr`` file with the given metadata."""
    path = session_dir / f"{name}.kohakutr"
    store = SessionStore(path)
    store.init_meta(
        session_id=name,
        config_type="agent",
        config_path="",
        pwd=str(session_dir),
        agents=agents or ["root"],
    )
    if lineage is not None:
        store.meta["lineage"] = lineage
    if forked_children is not None:
        store.meta["forked_children"] = forked_children
    store.close(update_status=False)
    return path


def _seed_turn(
    path: Path,
    agent: str,
    turn_index: int,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cached: int = 0,
    cost_usd: float | None = None,
    tool_calls: int = 0,
    error: bool = False,
    compact: bool = False,
) -> None:
    """Append a representative event sequence for one turn + its rollup."""
    store = SessionStore(path)
    store.append_event(
        agent, "user_input", {"content": f"turn {turn_index}"}, turn_index=turn_index
    )
    for _ in range(tool_calls):
        store.append_event(agent, "tool_call", {"tool": "x"}, turn_index=turn_index)
    if error:
        store.append_event(
            agent, "tool_error", {"error": "boom"}, turn_index=turn_index
        )
    if compact:
        store.append_event(
            agent, "compact_complete", {"round": 1}, turn_index=turn_index
        )
    store.save_turn_rollup(
        agent,
        turn_index,
        {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_cached": tokens_cached,
            "cost_usd": cost_usd,
        },
    )
    store.close(update_status=False)


# ─────────────────────────────────────────────────────────────────────
# Pub-sub on append_event (used by the live-attach WS)
# ─────────────────────────────────────────────────────────────────────


class TestPubSub:
    def test_subscribe_receives_appended_events(self, tmp_path: Path):
        store = SessionStore(tmp_path / "p.kohakutr")
        seen: list[tuple[str, dict]] = []
        store.subscribe(lambda key, data: seen.append((key, dict(data))))

        store.append_event("root", "user_input", {"content": "hi"}, turn_index=1)
        store.append_event("root", "tool_call", {"tool": "x"}, turn_index=1)

        assert len(seen) == 2
        assert seen[0][1]["type"] == "user_input"
        assert seen[1][1]["type"] == "tool_call"
        # Key is ``<agent>:e<seq>``.
        assert seen[0][0].startswith("root:e")

    def test_unsubscribe_stops_delivery(self, tmp_path: Path):
        store = SessionStore(tmp_path / "p.kohakutr")
        seen: list[tuple[str, dict]] = []
        cb = lambda key, data: seen.append((key, dict(data)))  # noqa: E731
        store.subscribe(cb)
        store.append_event("root", "user_input", {"content": "1"}, turn_index=1)
        store.unsubscribe(cb)
        store.append_event("root", "user_input", {"content": "2"}, turn_index=1)
        assert len(seen) == 1

    def test_unsubscribe_unknown_callback_is_safe(self, tmp_path: Path):
        store = SessionStore(tmp_path / "p.kohakutr")
        store.unsubscribe(lambda *_: None)  # No raise.

    def test_subscribe_idempotent(self, tmp_path: Path):
        store = SessionStore(tmp_path / "p.kohakutr")
        seen: list = []
        cb = lambda *args: seen.append(args)  # noqa: E731
        store.subscribe(cb)
        store.subscribe(cb)  # Same callable — must not register twice.
        store.append_event("root", "user_input", {}, turn_index=1)
        assert len(seen) == 1

    def test_failing_subscriber_does_not_block_others(self, tmp_path: Path):
        """A buggy listener must not stop sibling listeners or the
        underlying append from completing — the live-attach WS should
        not be able to take the agent down."""
        store = SessionStore(tmp_path / "p.kohakutr")
        seen: list = []
        store.subscribe(lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        store.subscribe(lambda key, data: seen.append(key))

        key, _eid = store.append_event("root", "user_input", {}, turn_index=1)
        assert seen == [key]


# ─────────────────────────────────────────────────────────────────────
# /tree
# ─────────────────────────────────────────────────────────────────────


class TestTreeEndpoint:
    def test_standalone_session(self, app_with_sessions):
        app, tmp = app_with_sessions
        _make_session(tmp, "alice")
        client = TestClient(app)

        resp = client.get("/api/sessions/alice/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_name"] == "alice"
        # Just the focus node, no edges.
        nodes = data["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["id"] == "alice"
        assert nodes[0]["is_focus"] is True
        assert data["edges"] == []

    def test_with_parent_and_child_forks(self, app_with_sessions):
        app, tmp = app_with_sessions
        _make_session(
            tmp,
            "child",
            lineage={"fork": {"parent_session_id": "parent", "fork_point": 7}},
            forked_children=[
                {
                    "session_id": "grandchild",
                    "path": str(tmp / "grandchild.kohakutr"),
                    "fork_point": 12,
                    "fork_created_at": "2026-01-01T00:00:00",
                }
            ],
        )
        client = TestClient(app)

        data = client.get("/api/sessions/child/tree").json()
        ids = {n["id"] for n in data["nodes"]}
        assert ids == {"child", "parent", "grandchild"}
        # Parent edge points INTO child, fork edge from child to grandchild.
        edges = data["edges"]
        assert {"from": "parent", "to": "child", "type": "fork", "at": 7} in edges
        assert {
            "from": "child",
            "to": "grandchild",
            "type": "fork",
            "at": 12,
        } in edges

    def test_with_attached_agents(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "host")
        store = SessionStore(path)
        # Attached-agent namespace is recovered from event keys.
        store.append_event(
            "host:attached:reader:1",
            "agent_attached",
            {"role": "reader"},
            turn_index=1,
        )
        store.close(update_status=False)
        client = TestClient(app)

        data = client.get("/api/sessions/host/tree").json()
        attached = [n for n in data["nodes"] if n["type"] == "attached"]
        assert len(attached) == 1
        assert attached[0]["host"] == "host"
        assert attached[0]["role"] == "reader"
        assert {
            "from": "host",
            "to": "host:attached:reader:1",
            "type": "attach",
        } in data["edges"]

    def test_missing_session_404(self, app_with_sessions):
        app, _ = app_with_sessions
        client = TestClient(app)
        assert client.get("/api/sessions/nope/tree").status_code == 404


# ─────────────────────────────────────────────────────────────────────
# /summary
# ─────────────────────────────────────────────────────────────────────


class TestSummaryEndpoint:
    def test_aggregates_tokens_cost_and_hot_turns(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        _seed_turn(path, "root", 1, tokens_in=100, tokens_out=50, cost_usd=0.01)
        _seed_turn(
            path,
            "root",
            2,
            tokens_in=2000,
            tokens_out=200,
            tokens_cached=800,
            cost_usd=0.50,
            tool_calls=3,
        )
        _seed_turn(path, "root", 3, tokens_in=300, cost_usd=0.02, error=True)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/summary").json()

        totals = data["totals"]
        assert totals["turns"] == 3
        assert totals["tokens"]["prompt"] == 2400
        assert totals["tokens"]["completion"] == 250
        assert totals["tokens"]["cached"] == 800
        # Three turns × 0.01 / 0.50 / 0.02 = 0.53 (allow float slack).
        assert abs(totals["cost_usd"] - 0.53) < 1e-6
        assert totals["tool_calls"] == 3
        assert totals["errors"] == 1
        assert data["error_turns"] == [3]

        # Hottest turn = #2 ($0.50).
        assert data["hot_turns"][0]["turn_index"] == 2
        assert data["hot_turns"][0]["cost_usd"] == 0.50

    def test_hot_turns_falls_back_to_token_volume(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        # No costs — provider doesn't surface them.
        _seed_turn(path, "root", 1, tokens_in=100, tokens_out=10)
        _seed_turn(path, "root", 2, tokens_in=999, tokens_out=1)
        _seed_turn(path, "root", 3, tokens_in=10, tokens_out=10)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/summary").json()
        assert data["totals"]["cost_usd"] is None
        # Hottest by token volume = turn 2 (1000 total tokens).
        assert data["hot_turns"][0]["turn_index"] == 2

    def test_filters_by_agent(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice", agents=["root", "helper"])
        _seed_turn(path, "root", 1, tokens_in=100, cost_usd=0.01)
        _seed_turn(path, "helper", 1, tokens_in=999, cost_usd=0.99)

        client = TestClient(app)
        # Default (all agents) sees both.
        all_data = client.get("/api/sessions/alice/summary").json()
        assert all_data["totals"]["turns"] == 2
        # Filtered to "root" only.
        root_data = client.get("/api/sessions/alice/summary?agent=root").json()
        assert root_data["totals"]["turns"] == 1
        assert root_data["agents"] == ["root"]

    def test_unknown_agent_404(self, app_with_sessions):
        app, tmp = app_with_sessions
        _make_session(tmp, "alice")
        client = TestClient(app)
        assert client.get("/api/sessions/alice/summary?agent=ghost").status_code == 404


# ─────────────────────────────────────────────────────────────────────
# /turns
# ─────────────────────────────────────────────────────────────────────


class TestTurnsEndpoint:
    def test_paginated_range(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        for ti in range(1, 11):
            _seed_turn(path, "root", ti, tokens_in=ti)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/turns?limit=4&offset=2").json()
        assert data["total"] == 10
        assert data["offset"] == 2
        assert [t["turn_index"] for t in data["turns"]] == [3, 4, 5, 6]

    def test_from_to_range_filter(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        for ti in range(1, 11):
            _seed_turn(path, "root", ti)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/turns?from_turn=4&to_turn=6").json()
        assert [t["turn_index"] for t in data["turns"]] == [4, 5, 6]
        assert data["total"] == 3

    def test_default_picks_first_agent(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice", agents=["root", "helper"])
        _seed_turn(path, "root", 1, tokens_in=100)
        _seed_turn(path, "helper", 1, tokens_in=999)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/turns").json()
        assert data["agent"] == "root"
        assert data["turns"][0]["tokens_in"] == 100

    def test_unknown_agent_404(self, app_with_sessions):
        app, tmp = app_with_sessions
        _make_session(tmp, "alice")
        client = TestClient(app)
        assert client.get("/api/sessions/alice/turns?agent=ghost").status_code == 404


# ─────────────────────────────────────────────────────────────────────
# /events
# ─────────────────────────────────────────────────────────────────────


class TestEventsEndpoint:
    def test_filter_by_turn_index_and_type(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        _seed_turn(path, "root", 1, tool_calls=2)
        _seed_turn(path, "root", 2, tool_calls=1, error=True)

        client = TestClient(app)
        # Only turn 2's tool_call + tool_error events.
        resp = client.get(
            "/api/sessions/alice/events?turn_index=2&types=tool_call,tool_error"
        ).json()
        types = [e["type"] for e in resp["events"]]
        assert types == ["tool_call", "tool_error"]
        assert all(e["turn_index"] == 2 for e in resp["events"])

    def test_cursor_pagination(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        for ti in range(1, 6):
            _seed_turn(path, "root", ti)

        client = TestClient(app)
        first = client.get("/api/sessions/alice/events?limit=3").json()
        assert first["count"] == 3
        assert first["next_cursor"] is not None

        second = client.get(
            f"/api/sessions/alice/events?limit=3&cursor={first['next_cursor']}"
        ).json()
        # Cursor advances strictly past the first batch.
        first_ids = {e["event_id"] for e in first["events"]}
        second_ids = {e["event_id"] for e in second["events"]}
        assert first_ids.isdisjoint(second_ids)

    def test_ts_range_filter(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        store = SessionStore(path)
        # Three events with controlled timestamps.
        store.append_event("root", "user_input", {"ts": 100.0}, turn_index=1)
        store.append_event("root", "user_input", {"ts": 200.0}, turn_index=2)
        store.append_event("root", "user_input", {"ts": 300.0}, turn_index=3)
        store.close(update_status=False)

        client = TestClient(app)
        resp = client.get("/api/sessions/alice/events?from_ts=150&to_ts=250").json()
        assert [e["ts"] for e in resp["events"]] == [200.0]

    def test_default_picks_first_agent(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice", agents=["root", "helper"])
        _seed_turn(path, "root", 1)
        _seed_turn(path, "helper", 1)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/events").json()
        assert data["agent"] == "root"

    def test_unknown_agent_404(self, app_with_sessions):
        app, tmp = app_with_sessions
        _make_session(tmp, "alice")
        client = TestClient(app)
        assert client.get("/api/sessions/alice/events?agent=ghost").status_code == 404


# ─────────────────────────────────────────────────────────────────────
# Empty-rollup fallback (regression for "Trace/Cost/Overview empty")
# ─────────────────────────────────────────────────────────────────────


class TestRollupFallback:
    """Sessions where ``_handle_turn_token_usage`` never wrote
    ``turn_rollup`` rows must still surface turn data, derived from
    the events table. Catches the bug that left every session's Trace,
    Cost, and Overview tabs empty even when the conversation was full.
    """

    def test_turns_endpoint_falls_back_to_events(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        store = SessionStore(path)
        # Append events for two turns WITHOUT writing rollups.
        for ti in (1, 2):
            store.append_event(
                "root", "user_input", {"content": f"q{ti}"}, turn_index=ti
            )
            store.append_event(
                "root",
                "turn_token_usage",
                {"prompt_tokens": 100 * ti, "completion_tokens": 10 * ti},
                turn_index=ti,
            )
            store.append_event("root", "tool_call", {"tool": "x"}, turn_index=ti)
        store.close(update_status=False)
        client = TestClient(app)

        data = client.get("/api/sessions/alice/turns").json()
        assert data["total"] == 2, "derived rows missing"
        # Synthesized rows carry the same shape as the rollup table.
        rows = {r["turn_index"]: r for r in data["turns"]}
        assert rows[1]["tokens_in"] == 100
        assert rows[2]["tokens_in"] == 200
        assert rows[1]["tool_calls"] == 1
        assert rows[2]["tool_calls"] == 1

    def test_summary_endpoint_falls_back_to_events(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        store = SessionStore(path)
        store.append_event("root", "user_input", {"content": "q"}, turn_index=1)
        store.append_event(
            "root",
            "turn_token_usage",
            {"prompt_tokens": 500, "completion_tokens": 50},
            turn_index=1,
        )
        store.close(update_status=False)
        client = TestClient(app)

        data = client.get("/api/sessions/alice/summary").json()
        # Pre-fix: every counter was zero because the rollup table is
        # empty. Post-fix: derived row populates these.
        assert data["totals"]["turns"] == 1
        assert data["totals"]["tokens"]["prompt"] == 500
        assert data["totals"]["tokens"]["completion"] == 50

    def test_real_rollup_rows_still_take_precedence(self, app_with_sessions):
        """When ``save_turn_rollup`` HAS run, the stored rows win over
        the events fallback (cheaper, more accurate)."""
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice")
        store = SessionStore(path)
        # Stored row says 999 tokens, event-derived would say 100.
        store.save_turn_rollup(
            "root",
            1,
            {"tokens_in": 999, "tokens_out": 1, "cost_usd": 0.5},
        )
        store.append_event(
            "root",
            "turn_token_usage",
            {"prompt_tokens": 100, "completion_tokens": 1},
            turn_index=1,
        )
        store.close(update_status=False)
        client = TestClient(app)

        data = client.get("/api/sessions/alice/turns").json()
        assert data["turns"][0]["tokens_in"] == 999, "rollup row not preferred"


class TestTurnsAggregate:
    """``/turns?aggregate=true`` sums every agent's per-turn rollup
    into a unified view with a per-agent ``breakdown``. Drives the
    Cost tab's "All agents combined" mode."""

    def test_aggregate_sums_main_plus_attached(self, app_with_sessions):
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice", agents=["root", "helper"])
        # Two main agents both contribute to turn 1.
        _seed_turn(path, "root", 1, tokens_in=100, tokens_out=10, cost_usd=0.01)
        _seed_turn(path, "helper", 1, tokens_in=200, tokens_out=20, cost_usd=0.02)
        # Plus an attached agent that wrote events (no rollup row),
        # exercising the events-derived path inside aggregation.
        store = SessionStore(path)
        store.append_event(
            "root:attached:reader:1",
            "turn_token_usage",
            {"prompt_tokens": 50, "completion_tokens": 5, "cached_tokens": 8},
            turn_index=1,
        )
        store.close(update_status=False)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/turns?aggregate=true").json()

        assert data["aggregate"] is True
        assert data["total"] == 1
        row = data["turns"][0]
        assert row["turn_index"] == 1
        assert row["tokens_in"] == 100 + 200 + 50
        assert row["tokens_out"] == 10 + 20 + 5
        assert row["tokens_cached"] == 8
        assert abs(row["cost_usd"] - 0.03) < 1e-6
        # Breakdown lists every agent.
        agents_in_breakdown = {b["agent"] for b in row["breakdown"]}
        assert agents_in_breakdown == {
            "root",
            "helper",
            "root:attached:reader:1",
        }
        # ``kind`` annotates main vs attached so the UI can colour them.
        kinds = {b["agent"]: b["kind"] for b in row["breakdown"]}
        assert kinds["root"] == "main"
        assert kinds["helper"] == "main"
        assert kinds["root:attached:reader:1"] == "attached"

    def test_aggregate_ignores_explicit_agent_param(self, app_with_sessions):
        """Aggregate mode disregards the per-agent filter — passing
        ``agent=`` alongside ``aggregate=true`` shouldn't 404 or scope."""
        app, tmp = app_with_sessions
        path = _make_session(tmp, "alice", agents=["root", "helper"])
        _seed_turn(path, "root", 1, tokens_in=100)
        _seed_turn(path, "helper", 1, tokens_in=200)

        client = TestClient(app)
        data = client.get("/api/sessions/alice/turns?aggregate=true&agent=ghost").json()
        assert data["aggregate"] is True
        assert data["turns"][0]["tokens_in"] == 300
