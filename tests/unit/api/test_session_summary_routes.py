"""Unit tests for the exchanges, summary-edit and summary-settings routes."""

from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.routes.identity import session_summary as settings_routes
from kohakuterrarium.api.routes.persistence import summary_edit, viewer
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import get_session_index_default
from kohakuterrarium.studio.sessions.lifecycle import start_creature
from kohakuterrarium.studio.sessions.registry import meta_for
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    path = tmp_path / "sessions" / "saved.kohakutr"
    with closing(SessionStore(str(path))) as store:
        store.init_meta("saved", "agent", "", str(tmp_path), ["main"])
        store.save_conversation(
            "main",
            [
                {"role": "user", "content": "Profile the importer"},
                {"role": "assistant", "content": "It spends 80% in parsing."},
            ],
        )
    app = FastAPI()
    for router in (summary_edit.router, viewer.router):
        app.include_router(router, prefix="/api/sessions")
    app.include_router(settings_routes.router, prefix="/api/settings")
    app.state.engine_pool = None
    app.state.auth_config = AuthConfig()
    return TestClient(app)


def test_exchanges_route_returns_the_quoted_exchange(client):
    body = client.get("/api/sessions/saved/exchanges?limit=2").json()
    assert body["exchanges"] == [
        {
            "turn": 1,
            "user": "Profile the importer",
            "reply": "It spends 80% in parsing.",
        }
    ]
    assert client.get("/api/sessions/nope/exchanges").status_code == 404


def test_summary_text_edit_and_refresh_on_a_saved_session(client, tmp_path):
    index = get_session_index_default(tmp_path / "sessions")
    assert index.get("saved.kohakutr")["summary"] == ""
    put = client.put(
        "/api/sessions/saved/summary/text", json={"text": " Importer  perf "}
    )
    assert put.json()["summary"]["text"] == "Importer perf"
    assert put.json()["summary"]["source"] == "user"
    assert [r["summary"] for r in index.list(search="importer").rows] == [
        "Importer perf"
    ]
    shown = client.get("/api/sessions/saved/exchanges").json()
    assert (shown["summary"], shown["summary_source"]) == ("Importer perf", "user")
    refreshed = client.post("/api/sessions/saved/summary/refresh").json()["summary"]
    assert (refreshed["text"], refreshed["source"]) == (
        "Profile the importer",
        "heuristic",
    )
    cleared = client.put("/api/sessions/saved/summary/text", json={"text": ""})
    assert cleared.json()["summary"] == {}
    assert (
        client.put("/api/sessions/nope/summary/text", json={"text": "x"}).status_code
        == 404
    )


def test_title_route_names_and_clears_a_saved_session(client, tmp_path):
    index = get_session_index_default(tmp_path / "sessions")
    assert index.get("saved.kohakutr")["title"] == ""
    named = client.put(
        "/api/sessions/saved/title", json={"title": "  Importer \n perf "}
    )
    assert named.json() == {"title": "Importer perf"}
    assert index.get("saved.kohakutr")["title"] == "Importer perf"
    assert (
        client.get("/api/sessions/saved/exchanges").json()["title"] == "Importer perf"
    )
    assert client.put("/api/sessions/saved/title", json={"title": ""}).json() == {
        "title": ""
    }
    assert index.get("saved.kohakutr")["title"] == ""
    assert (
        client.put("/api/sessions/nope/title", json={"title": "x"}).status_code == 404
    )


async def test_title_route_renames_a_running_session_live(tmp_path, monkeypatch):
    def scripted(*_args, **_kwargs):
        return ScriptedLLM(["OK"])

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", scripted)
    monkeypatch.setattr(_agent_init, "create_llm_provider", scripted)
    creature = tmp_path / "probe"
    creature.mkdir()
    (creature / "config.yaml").write_text(
        "name: probe\nsystem_prompt: test\ntools: []\nsubagents: []\n",
        encoding="utf-8",
    )
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        session = await start_creature(service, config_path=str(creature))
        sid = session.session_id
        body = summary_edit.TitleBody(title=" Live  name ")
        assert await summary_edit.put_title(sid, body, service=service) == {
            "title": "Live name"
        }
        assert engine._session_stores[sid].meta.get("name") == "Live name"
        assert meta_for(service)[sid]["name"] == "Live name"
    finally:
        await engine.shutdown()


def test_settings_route_reads_validates_and_reports_the_override(client, monkeypatch):
    monkeypatch.delenv("KT_SESSION_SUMMARY_SOURCE", raising=False)
    first = client.get("/api/settings/session-summary").json()
    assert (first["source"], first["every_n_turns"], first["source_override"]) == (
        "llm",
        5,
        None,
    )
    saved = client.put(
        "/api/settings/session-summary",
        json={"every_n_turns": 2, "model": "p/m", "ignored": 1},
    ).json()
    assert (saved["every_n_turns"], saved["model"]) == (2, "p/m")
    assert "ignored" not in saved
    bad = client.put("/api/settings/session-summary", json={"source": "magic"})
    assert bad.status_code == 400
    monkeypatch.setenv("KT_SESSION_SUMMARY_SOURCE", "off")
    overridden = client.get("/api/settings/session-summary").json()
    assert (overridden["source"], overridden["source_override"]) == ("llm", "off")
