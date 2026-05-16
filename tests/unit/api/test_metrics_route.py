"""Unit tests for :mod:`kohakuterrarium.api.routes.metrics`."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes import metrics as metrics_mod
from kohakuterrarium.studio.sessions.handles import Session, SessionListing


class _FakeAggregator:
    def __init__(self, snap=None):
        self._snap = snap or {"counters": {"a": 1}}

    def snapshot(self):
        return dict(self._snap)


class _FakeEngine:
    """Doubles as the ``service`` the metrics route receives.

    The route is service-routed (``Depends(get_service)``).
    ``_build_gauges`` runs ``host_engine_or_none(service)`` — for a
    plain object (not a ``TerrariumService`` Protocol, no
    ``connected_nodes``) that returns the object itself, so this fake
    serves as both the "service" handed to the route AND the host
    engine the MCP gauge walks.  ``list_sessions`` / ``get_session``
    are monkeypatched, so they ignore what is passed.
    """

    def __init__(self, creatures_map=None):
        self._creatures = creatures_map or {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]


def _client(engine, monkeypatch, *, aggregator=None, sessions=None, session_map=None):
    monkeypatch.setattr(
        metrics_mod, "get_aggregator", lambda: aggregator or _FakeAggregator()
    )
    monkeypatch.setattr(
        metrics_mod.sessions_lifecycle,
        "list_sessions",
        lambda svc: list(sessions or []),
    )
    monkeypatch.setattr(
        metrics_mod.sessions_lifecycle,
        "get_session",
        lambda svc, sid: (session_map or {}).get(sid)
        or Session(session_id=sid, name=sid),
    )
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: engine
    app.include_router(metrics_mod.router, prefix="/metrics")
    return TestClient(app)


# ── /snapshot ──────────────────────────────────────────────────


class TestMetricsSnapshot:
    def test_basic_no_sessions(self, monkeypatch):
        eng = _FakeEngine()
        client = _client(eng, monkeypatch)
        resp = client.get("/metrics/snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counters"] == {"a": 1}
        gauges = body["gauges"]
        assert gauges["sessions_open"] == 0
        assert gauges["agents_running"] == 0

    def test_solo_and_multi_classification(self, monkeypatch):
        eng = _FakeEngine()
        sessions = [
            SessionListing(session_id="g1", name="x", creatures=1),
            SessionListing(session_id="g2", name="y", creatures=3),
        ]
        session_map = {
            "g1": Session(
                session_id="g1",
                name="x",
                creatures=[{"creature_id": "c1"}],
            ),
            "g2": Session(
                session_id="g2",
                name="y",
                creatures=[
                    {"creature_id": "c2"},
                    {"creature_id": "c3"},
                    {"creature_id": "c4"},
                ],
            ),
        }
        client = _client(eng, monkeypatch, sessions=sessions, session_map=session_map)
        resp = client.get("/metrics/snapshot")
        gauges = resp.json()["gauges"]
        assert gauges["creatures_running"] == 1  # solo
        assert gauges["terrariums_running"] == 1  # multi
        assert gauges["sessions_open"] == 2
        assert gauges["agents_running"] == 4  # 1 + 3

    def test_mcp_servers_connected(self, monkeypatch):
        mgr = SimpleNamespace(_sessions={"server-a": object(), "server-b": object()})
        ag = SimpleNamespace(_mcp_manager=mgr)
        creature = SimpleNamespace(agent=ag)
        eng = _FakeEngine(creatures_map={"c1": creature})
        sessions = [SessionListing(session_id="g1", name="x", creatures=1)]
        session_map = {
            "g1": Session(
                session_id="g1",
                name="x",
                creatures=[{"creature_id": "c1"}],
            )
        }
        client = _client(eng, monkeypatch, sessions=sessions, session_map=session_map)
        gauges = client.get("/metrics/snapshot").json()["gauges"]
        assert gauges["mcp_servers_connected"] == 2

    def test_get_session_failure_swallowed(self, monkeypatch):
        eng = _FakeEngine()
        # ``get_session`` raises — the gauge computation tolerates it.
        monkeypatch.setattr(
            metrics_mod.sessions_lifecycle,
            "list_sessions",
            lambda eng: [SessionListing(session_id="g1", name="x", creatures=1)],
        )

        def boom(svc, sid):
            raise RuntimeError("dead")

        monkeypatch.setattr(metrics_mod.sessions_lifecycle, "get_session", boom)
        monkeypatch.setattr(metrics_mod, "get_aggregator", lambda: _FakeAggregator())
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: eng
        app.include_router(metrics_mod.router, prefix="/metrics")
        client = TestClient(app)
        resp = client.get("/metrics/snapshot")
        assert resp.status_code == 200
        gauges = resp.json()["gauges"]
        # The listing-derived gauges still count the session; only the
        # get_session-dependent gauges (agents_running, mcp) zero out
        # because that lookup raised and is swallowed per-session.
        assert gauges["sessions_open"] == 1
        assert gauges["creatures_running"] == 1
        assert gauges["agents_running"] == 0
        assert gauges["mcp_servers_connected"] == 0


# ── _build_gauges direct ───────────────────────────────────────


class TestBuildGauges:
    def test_handles_missing_creature_id(self, monkeypatch):
        eng = _FakeEngine()
        monkeypatch.setattr(
            metrics_mod.sessions_lifecycle,
            "list_sessions",
            lambda svc: [SessionListing(session_id="g1", name="x", creatures=1)],
        )
        monkeypatch.setattr(
            metrics_mod.sessions_lifecycle,
            "get_session",
            lambda svc, sid: Session(
                session_id=sid,
                name=sid,
                creatures=[{"name": "noid-creature"}],  # no creature_id
            ),
        )
        gauges = metrics_mod._build_gauges(eng)
        # creature_id missing → no MCP lookup attempted.
        assert gauges["mcp_servers_connected"] == 0
