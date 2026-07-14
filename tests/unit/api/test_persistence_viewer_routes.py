"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.viewer`."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import viewer as viewer_mod


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(viewer_mod.router, prefix="/sessions")
    return app


@pytest.fixture
def _patch_resolve(monkeypatch):
    """Patch the path resolver + the _run_with_store wrapper to bypass
    real SessionStore IO during these tests."""

    def install(*, resolve_returns=Path("/x/s.kohakutr")):
        monkeypatch.setattr(
            viewer_mod, "resolve_session_path_default", lambda n: resolve_returns
        )

    return install


# ── tree ───────────────────────────────────────────────────────


class TestTree:
    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app())
        resp = client.get("/sessions/ghost/tree")
        assert resp.status_code == 404

    def test_success(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: {"nodes": ["n1"], "edges": ["e1"]},
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/tree")
        assert resp.status_code == 200
        # Route returns the builder payload verbatim.
        assert resp.json() == {"nodes": ["n1"], "edges": ["e1"]}


# ── summary ────────────────────────────────────────────────────


def _real_session(tmp_path) -> Path:
    """Build a minimal real session file so _run_with_store + the
    per-endpoint ``_build`` closures actually execute."""
    from kohakuterrarium.session.store import SessionStore

    path = tmp_path / "alice.kohakutr"
    s = SessionStore(str(path))
    try:
        s.init_meta("alice", "agent", "/p", "/w", ["alice"])
    finally:
        s.close()
    return path


class TestSummary:
    def test_success(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: {"session_name": "x", "turns": 7},
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/summary")
        assert resp.status_code == 200
        assert resp.json() == {"session_name": "x", "turns": 7}

    def test_build_closure_runs_against_real_store(self, monkeypatch, tmp_path):
        # Don't stub _run_with_store — let it open the real session and
        # invoke the endpoint's ``_build`` closure, which forwards
        # (store, canonical, agent) to build_summary_payload.
        path = _real_session(tmp_path)
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: path)
        captured = {}

        def _fake_build(store, canonical, agent):
            captured["canonical"] = canonical
            captured["agent"] = agent
            return {"session_name": canonical}

        monkeypatch.setattr(viewer_mod, "build_summary_payload", _fake_build)
        resp = TestClient(_app()).get("/sessions/alice/summary?agent=alice")
        assert resp.status_code == 200
        assert resp.json() == {"session_name": "alice"}
        # The closure passed the normalized stem + the agent query param.
        assert captured["canonical"] == "alice"
        assert captured["agent"] == "alice"


# ── live-session resolution (graph_id ≠ on-disk file stem) ──────


class TestLiveSessionResolution:
    def test_viewer_and_history_resolve_a_live_graph_id(self, monkeypatch, tmp_path):
        # A live session is addressed by its graph_id, but its autosession
        # file is named by creature_id — so on-disk name resolution misses
        # it. The routes must resolve it from the engine's attached store.
        import types

        from kohakuterrarium.api.deps import get_service
        from kohakuterrarium.api.routes.persistence import history as history_mod
        from kohakuterrarium.session.store import SessionStore

        graph_id = "graph_abcdef123456"
        # Deliberately creature_id-named, NOT the graph_id.
        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.append_event(
            "alice",
            "turn_token_usage",
            {"prompt_tokens": 10, "completion_tokens": 4},
            turn_index=1,
        )
        store.checkpoint()  # flush + WAL checkpoint so the read store sees it

        engine = types.SimpleNamespace(_session_stores={graph_id: store})

        # On-disk name resolution must MISS the graph_id, proving the live
        # lookup is what resolves it.
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: None)
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda n: None)

        app = FastAPI()
        app.include_router(viewer_mod.router, prefix="/sessions")
        app.include_router(history_mod.router, prefix="/sessions")
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            # Viewer tab (Overview summary) — 200 + real data, not 404.
            summary = client.get(f"/sessions/{graph_id}/summary")
            assert summary.status_code == 200
            assert "alice" in summary.json()["agents"]

            # History index (Overview metadata) — 200, not 404.
            history = client.get(f"/sessions/{graph_id}/history")
            assert history.status_code == 200

            # A genuinely-unknown id still 404s (falls through to on-disk).
            missing = client.get("/sessions/graph_ghost/summary")
            assert missing.status_code == 404
        finally:
            store.close()

    def test_live_viewer_reuses_the_attached_store(self, monkeypatch, tmp_path):
        # THE CI bug (POSIX): a second SessionStore open of the live,
        # actively-written file fails with SQLITE_IOERR. While the
        # session is live, every viewer read — addressed by graph_id OR
        # by the store's file stem — must reuse the engine's open store,
        # so constructing a new store (and on-disk name resolution) is
        # bombed.
        import types

        from kohakuterrarium.api.deps import get_service
        from kohakuterrarium.session.store import SessionStore
        from kohakuterrarium.studio.persistence.viewer import diff as diff_mod

        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        engine = types.SimpleNamespace(_session_stores={"graph_live": store})

        def _bomb(*a, **k):
            raise AssertionError("live viewer read must not reopen the session file")

        monkeypatch.setattr(viewer_mod, "SessionStore", _bomb)
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(diff_mod, "SessionStore", _bomb)

        app = FastAPI()
        app.include_router(viewer_mod.router, prefix="/sessions")
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            for name in ("graph_live", "alice_3f2a9c11"):
                for noun in ("summary", "tree", "turns", "events"):
                    resp = client.get(f"/sessions/{name}/{noun}")
                    assert resp.status_code == 200, (name, noun, resp.text)
                export = client.get(f"/sessions/{name}/export")
                assert export.status_code == 200, (name, export.text)
                # Self-diff while live: both sides reuse the open store.
                diff = client.get(f"/sessions/{name}/diff", params={"other": name})
                assert diff.status_code == 200, (name, diff.text)
                assert diff.json()["identical"] is True
        finally:
            store.close()


# ── turns ──────────────────────────────────────────────────────


class TestTurns:
    def test_success(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: {"turns": [{"index": 0}]},
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/turns?limit=10&offset=0")
        assert resp.status_code == 200
        assert resp.json() == {"turns": [{"index": 0}]}

    def test_build_closure_clamps_limit_and_offset(self, monkeypatch, tmp_path):
        # The turns ``_build`` closure clamps limit to [1,1000] and
        # offset to >=0 before handing them to build_turns_payload.
        path = _real_session(tmp_path)
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: path)
        captured = {}

        def _fake_build(store, canonical, **kw):
            captured.update(kw)
            return {"turns": []}

        monkeypatch.setattr(viewer_mod, "build_turns_payload", _fake_build)
        resp = TestClient(_app()).get("/sessions/alice/turns?limit=99999&offset=-5")
        assert resp.status_code == 200
        # limit clamped to the 1000 ceiling, offset floored at 0.
        assert captured["limit"] == 1000
        assert captured["offset"] == 0


# ── export ─────────────────────────────────────────────────────


class TestExport:
    def test_default_md(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: ("text/markdown", "# session"),
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/export")
        assert resp.status_code == 200
        # Body + content-type come from the builder; filename uses the
        # normalized session stem with the .md extension.
        assert resp.text == "# session"
        assert resp.headers["content-type"].startswith("text/markdown")
        assert resp.headers["content-disposition"] == 'attachment; filename="s.md"'

    def test_html(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: ("text/html", "<html></html>"),
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/export?format=html")
        assert resp.status_code == 200
        assert resp.text == "<html></html>"
        assert resp.headers["content-type"].startswith("text/html")
        assert resp.headers["content-disposition"] == 'attachment; filename="s.html"'

    def test_build_closure_lowercases_format(self, monkeypatch, tmp_path):
        # The export ``_build`` closure forwards a lower-cased format
        # string to build_export.
        path = _real_session(tmp_path)
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: path)
        captured = {}

        def _fake_export(store, canonical, fmt, agent):
            captured["fmt"] = fmt
            return ("application/jsonl", b"{}")

        monkeypatch.setattr(viewer_mod, "build_export", _fake_export)
        resp = TestClient(_app()).get("/sessions/alice/export?format=JSONL")
        assert resp.status_code == 200
        assert captured["fmt"] == "jsonl"


# ── diff ───────────────────────────────────────────────────────


class TestDiff:
    def test_other_missing(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        # First resolve succeeds; second is None.
        calls = []

        def fake_resolve(name):
            calls.append(name)
            if len(calls) == 1:
                return Path("/x/a.kohakutr")
            return None

        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", fake_resolve)
        client = TestClient(_app())
        resp = client.get("/sessions/sess/diff?other=ghost")
        assert resp.status_code == 404

    def test_success(self, monkeypatch, _patch_resolve):
        monkeypatch.setattr(
            viewer_mod,
            "resolve_session_path_default",
            lambda n: Path(f"/x/{n}.kohakutr"),
        )
        captured = {}

        def fake_diff(a, b, agent=None):
            captured["a"] = a
            captured["b"] = b
            return {"diff": [{"change": "added"}]}

        monkeypatch.setattr(viewer_mod, "build_diff_payload", fake_diff)
        client = TestClient(_app())
        resp = client.get("/sessions/a/diff?other=b")
        assert resp.status_code == 200
        assert resp.json() == {"diff": [{"change": "added"}]}
        # Both session names resolved to their paths and passed through.
        assert captured["a"] == Path("/x/a.kohakutr")
        assert captured["b"] == Path("/x/b.kohakutr")


# ── events ─────────────────────────────────────────────────────


class TestEvents:
    def test_success(self, monkeypatch, _patch_resolve):
        _patch_resolve()
        monkeypatch.setattr(
            viewer_mod,
            "_run_with_store",
            lambda path, builder: {"events": [{"type": "text"}]},
        )
        client = TestClient(_app())
        resp = client.get("/sessions/sess/events?limit=5")
        assert resp.status_code == 200
        assert resp.json() == {"events": [{"type": "text"}]}

    def test_build_closure_forwards_filters(self, monkeypatch, tmp_path):
        # The events ``_build`` closure forwards the type / turn / ts
        # filters and clamps limit to build_events_payload.
        path = _real_session(tmp_path)
        monkeypatch.setattr(viewer_mod, "resolve_session_path_default", lambda n: path)
        captured = {}

        def _fake_build(store, canonical, **kw):
            captured.update(kw)
            return {"events": []}

        monkeypatch.setattr(viewer_mod, "build_events_payload", _fake_build)
        resp = TestClient(_app()).get(
            "/sessions/alice/events?turn_index=3&types=text&limit=5000"
        )
        assert resp.status_code == 200
        assert captured["turn_index"] == 3
        assert captured["types"] == "text"
        # limit clamped to the 1000 ceiling.
        assert captured["limit"] == 1000


# ── _run_with_store helper ─────────────────────────────────────


class TestRunWithStore:
    def test_normalises_and_closes(self, tmp_path, monkeypatch):
        # Build a real session file via SessionStore so the close hook is exercised.
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "alice.kohakutr"
        s = SessionStore(str(path))
        try:
            s.init_meta("alice", "agent", "/p", "/w", ["a"])
        finally:
            s.close()

        captured = {}

        def builder(store, canonical):
            captured["canonical"] = canonical
            return {"ok": True}

        out = viewer_mod._run_with_store(path, builder)
        assert out == {"ok": True}
        # canonical is the normalized stem of the path.
        assert captured["canonical"] == "alice"
